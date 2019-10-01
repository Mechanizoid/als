"""
Provides everything need to handle ALS main inputs : images.

We need to read file and in the future, get images from INDI
"""
import logging
import time
from pathlib import Path
from queue import Queue

import numpy as np
import rawpy
from rawpy._rawpy import LibRawNonFatalError, LibRawFatalError
from PyQt5.QtCore import QFileInfo
from astropy.io import fits
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from als import config
from als.code_utilities import log
from als.model import Image, STORE

_LOGGER = logging.getLogger(__name__)

# queue to which are posted all loaded images
_IMAGE_INPUT_QUEUE = Queue()

_IGNORED_FILENAME_START_PATTERNS = ['.', '~', 'tmp']
_DEFAULT_SCAN_FILE_SIZE_RETRY_PERIOD_IN_SEC = 0.1


class FolderScanner(FileSystemEventHandler):
    """
    Watches file changes (creation, move) in a specific filesystem folder

    the watched directory is retrieved from user config on scanner startup
    """

    @log
    def __init__(self):
        FileSystemEventHandler.__init__(self)
        self._observer = None

    @log
    def start(self):
        """
        Starts scanning scan folder for new files
        """
        self._observer = PollingObserver()
        self._observer.schedule(self, config.get_scan_folder_path(), recursive=False)
        self._observer.start()
        _LOGGER.info("Folder scanner started")
        STORE.scan_in_progress = True

    @log
    def pause(self):
        """
        Pauses scanning scan folder for new files

        Scanner is stopped
        """
        self._stop_observer()

    @log
    def stop(self):
        """
        Stops scanning scan folder for new files

        Scanner is stopped and input queue is purged
        """
        self._stop_observer()
        _purge_queue()

    @log
    def on_moved(self, event):
        if event.event_type == 'moved':
            image_path = event.dest_path
            _LOGGER.debug(f"File move detected : {image_path}")

            _enqueue_image(read_image(Path(image_path)))

    @log
    def on_created(self, event):
        if event.event_type == 'created':
            file_is_incomplete = True
            last_file_size = -1
            image_path = event.src_path
            _LOGGER.debug(f"File creation detected : {image_path}. Waiting until file is fully written to disk...")

            while file_is_incomplete:
                info = QFileInfo(image_path)
                size = info.size()
                _LOGGER.debug(f"File {image_path}'s size = {size}")
                if size == last_file_size:
                    file_is_incomplete = False
                    _LOGGER.debug(f"File {image_path} has been fully written to disk")
                last_file_size = size
                time.sleep(_DEFAULT_SCAN_FILE_SIZE_RETRY_PERIOD_IN_SEC)

            _enqueue_image(read_image(Path(image_path)))

    @log
    def _stop_observer(self):
        if self._observer is not None:
            _LOGGER.info("Stopping folder scanner... Waiting for current operation to complete...")
            self._observer.stop()
            self._observer = None
            _LOGGER.info("Folder scanner stopped")
            STORE.scan_in_progress = False


@log
def _purge_queue():
    while not _IMAGE_INPUT_QUEUE.empty():
        _IMAGE_INPUT_QUEUE.get()
    _LOGGER.info("Input queue purged")


@log
def _enqueue_image(image: Image):
    if image is not None:
        _IMAGE_INPUT_QUEUE.put(image)
        _LOGGER.info(f"Input queue size = {_IMAGE_INPUT_QUEUE.qsize()}")


@log
def read_image(path: Path):
    """
    Reads an image from disk

    :param path: path to the file to load image from
    :type path:  pathlib.Path

    :return: the image read from disk or None if image is ignored or an error occurred
    :rtype: Image or None
    """

    ignore_image = False
    image = None

    for pattern in _IGNORED_FILENAME_START_PATTERNS:
        if path.name.startswith(pattern):
            ignore_image = True
            break

    if not ignore_image:
        if path.suffix.lower() in ['.fit', '.fits']:
            image = _read_fit_image(path)
        else:
            image = _read_raw_image(path)

        if image is not None:
            file_path_str = str(path.resolve())
            image.origin = f"FILE : {file_path_str}"
            _LOGGER.info(f"Successful image read from file '{file_path_str}'")

    return image


@log
def _read_fit_image(path: Path):
    """
    read FIT image from filesystem

    :param path: path to image file to load from
    :type path: pathlib.Path

    :return: the loaded image, with data and headers parsed or None if a known error occurred
    :rtype: Image or None
    """
    try:
        with fits.open(str(path.resolve())) as fit:
            # pylint: disable=E1101
            data = fit[0].data
            header = fit[0].header

        image = Image(data)

        if 'BAYERPAT' in header:
            image.bayer_pattern = header['BAYERPAT']

    except OSError as error:
        _report_error(path, error)
        return None

    return image


@log
def _read_raw_image(path: Path):
    """
    Reads a RAW DLSR image from file

    :param path: path to the file to read from
    :type path: pathlib.Path

    :return: the image or None if a known error occurred
    :rtype: Image or None
    """

    try:
        with rawpy.imread(str(path.resolve())) as raw_image:

            # in here, we make sure we store the bayer pattern as it would be advertised if image was a FITS image.
            #
            # lets assume image comes from a DSLR sensor with the most common bayer pattern.
            #
            # The actual/physical bayer pattern would look like a repetition of :
            #
            # +---+---+
            # | R | G |
            # +---+---+
            # | G | B |
            # +---+---+
            #
            # RawPy will report the CFA description as 2 discrete values :
            #
            # 1) raw_image.raw_pattern : a 2x2 numpy array representing the indices used to express the bayer patten
            #
            # in our example, its value is :
            #
            # +---+---+
            # | 0 | 1 |
            # +---+---+
            # | 3 | 2 |
            # +---+---+
            #
            # and its flatten version is :
            #
            # [0, 1, 3, 2]
            #
            # 2) raw_image.color_desc : a bytes literal formed of the color of each pixel of the bayer pattern, in ascending
            #                           index order from raw_image.raw_pattern
            #
            # in our example, its value is : b'RGBG'
            #
            # We need to express/store this pattern in a more common way, i.e. as it would be described in a FITS header.
            # Or put simply, we want to express the bayer pattern as it would be described if raw_image.raw_pattern was :
            #
            # +---+---+
            # | 0 | 1 |
            # +---+---+
            # | 2 | 3 |
            # +---+---+
            indices = raw_image.raw_pattern.flatten()
            color_desc = raw_image.color_desc.decode()

            assert len(indices) == len(color_desc)
            bayer_pattern = ""
            for i in range(len(indices)):
                assert indices[i] < len(indices)
                bayer_pattern += color_desc[indices[i]]

            new_image = Image(raw_image.raw_image_visible)
            new_image.bayer_pattern = bayer_pattern

            return new_image

    except LibRawNonFatalError as non_fatal_error:
        _report_error(path, non_fatal_error)
        return None
    except LibRawFatalError as fatal_error:
        _report_error(path, fatal_error)
        return None


def _report_error(path: Path, error: Exception):
    _LOGGER.error(f"Error reading from file {str(path.resolve())} : {str(error)}")

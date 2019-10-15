"""
Stores all data needed and shared by app modules
"""
from queue import Queue

from PyQt5.QtCore import pyqtSignal, QObject
import numpy as np

import als
from als.code_utilities import log

VERSION = als.__version__

STACKING_MODE_SUM = "Sum"
STACKING_MODE_MEAN = "Mean"


class SignalingQueue(Queue, QObject):
    """
    Queue subclass that emits Qt signals when items are added or removed from the queue.

    Signals are :

      - item_pushed_signal
      - item_popped_signal

    And they both carry the queue's size after the signaled operation
    """

    item_pushed_signal = pyqtSignal(int)
    """
    Qt signal stating that a new item has just been pushed to the queue.

    :param: the new size of the queue
    :type: int
    """

    item_popped_signal = pyqtSignal(int)
    """
    Qt signal stating that a new item has just been popped from the queue.

    :param: the new size of the queue
    :type: int
    """

    def __init__(self, maxsize=0):
        Queue.__init__(self, maxsize)
        QObject.__init__(self)

    def get(self, block=True, timeout=None):
        item = super().get(block, timeout)
        self.item_popped_signal.emit(self.qsize())
        return item

    def get_nowait(self):
        item = super().get_nowait()
        self.item_popped_signal.emit(self.qsize())
        return item

    def put(self, item, block=True, timeout=None):
        super().put(item, block, timeout)
        self.item_pushed_signal.emit(self.qsize())

    def put_nowait(self, item):
        super().put_nowait(item)
        self.item_pushed_signal.emit(self.qsize())


class Session(QObject):
    """
    Represents an ALS session
    """

    stopped = 0
    running = 1
    paused = 2

    _ALLOWED_STATUSES = [stopped, running, paused]

    status_changed_signal = pyqtSignal()
    """Qt signal to emit when status changes"""

    @log
    def __init__(self, status: int = stopped):
        QObject.__init__(self)
        if status in Session._ALLOWED_STATUSES:
            self._status = status

    def is_running(self):
        """
        Is session running ?

        :return: True if session is running, False otherwise
        :rtype: bool
        """
        return self._status == Session.running

    def is_stopped(self):
        """
        Is session stopped ?

        :return: True if session is stopped, False otherwise
        :rtype: bool
        """
        return self._status == Session.stopped

    def is_paused(self):
        """
        Is session paused ?

        :return: True if session is paused, False otherwise
        :rtype: bool
        """
        return self._status == Session.paused

    def set_status(self, status: int):
        """
        Sets session status

        :param status: the status to set
        :type status: int
        """
        if status in Session._ALLOWED_STATUSES:
            self._status = status
            Session.status_changed_signal.emit()


# pylint: disable=R0902
class DataStore:
    """
    Holds and maintain application dynamic data
    """
    def __init__(self):
        self._observers = []
        self._session_is_started = False
        self._session_is_stopped = True
        self._session_is_paused = False
        self._web_server_is_running = False
        self._stacking_mode = ""
        self._align_before_stacking = True
        self._process_result = None
        self._input_queue = SignalingQueue()
        self._stack_queue = SignalingQueue()
        self._process_queue = SignalingQueue()
        self._save_queue = SignalingQueue()

    @property
    def save_queue(self):
        """
        Retrieves save queue

        :return: the save queue
        :rtype: SignalingQueue
        """
        return self._save_queue

    @property
    def process_result(self):
        """
        Retrieves latest published process result

        :return: the latest published process result
        :rtype: Image
        """
        return self._process_result

    @process_result.setter
    def process_result(self, image):
        """
        Record the latest process result

        :param image: the latest process result
        :type: Image
        """
        self._process_result = image

    @property
    def align_before_stacking(self):
        """
        Retrieves flag set if alignment is ON

        :return: is alignment ON ?
        :rtype: bool
        """
        return self._align_before_stacking

    @align_before_stacking.setter
    def align_before_stacking(self, align: bool):
        self._align_before_stacking = align

    @property
    def stacking_mode(self):
        """
        Retrieves stacking mode.

        :return: String representation of user chosen stacking mode.
        :rtype: str
        """
        return self._stacking_mode

    @stacking_mode.setter
    def stacking_mode(self, text: str):
        """
        Sets stacking mode.

        :param text: String representation of user chosen stacking mode. Allowed values are :

          - Sum : Stacking mode is sum
          - Mean : stacking mode is mean

        :type text: str
        """
        if text.strip() in [STACKING_MODE_MEAN, STACKING_MODE_SUM]:
            self._stacking_mode = text
        else:
            self._stacking_mode = STACKING_MODE_MEAN

    @property
    def process_queue(self):
        """
        Retrieves the processing queue

        :return: the processing queue
        :rtype: SignalingQueue
        """
        return self._process_queue

    @property
    def stack_queue(self):
        """
        Retrieves the stack queue

        :return: the stack queue
        :type: SignalingQueue
        """
        return self._stack_queue

    @property
    def input_queue(self):
        """
        Retrieves the input queue.

        :return: the main input queue
        :rtype: SignalingQueue
        """
        return self._input_queue

    @property
    @log
    def web_server_is_running(self):
        """
        Is web server running.

        :return: True if webserver is running, False otherwise
        :rtype: bool
        """
        return self._web_server_is_running

    @web_server_is_running.setter
    @log
    def web_server_is_running(self, running):
        """
        Sets flag for webserver running status.

        :param running: is webserver running
        :type running: bool
        """
        self._web_server_is_running = running
        self._notify_observers()

    @property
    @log
    def session_is_started(self):
        """
        Is session started.

        :return: True if session is started, False otherwise
        :rtype: bool
        """
        return self._session_is_started

    @log
    def record_session_start(self):
        """
        Sets flag for session started status.
        """
        self._session_is_started = True
        self._session_is_stopped = False
        self._session_is_paused = False
        self._notify_observers()

    @property
    @log
    def session_is_stopped(self):
        """
        Is session stopped.

        :return: True if session is stopped, False otherwise
        :rtype: bool
        """
        return self._session_is_stopped

    @log
    def record_session_stop(self):
        """
        Sets flag for session stopped status.
        """
        self._session_is_started = False
        self._session_is_stopped = True
        self._session_is_paused = False
        self._notify_observers()

    @property
    @log
    def session_is_paused(self):
        """
        Is session paused.

        :return: True if session is paused, False otherwise
        :rtype: bool
        """
        return self._session_is_paused

    @log
    def record_session_pause(self):
        """
        Sets flag for session paused status.
        """
        self._session_is_started = False
        self._session_is_stopped = False
        self._session_is_paused = True
        self._notify_observers()

    @log
    def add_observer(self, observer):
        """
        Adds an observer to our observers list.

        :param observer: the new observer
        :type observer: any
        """
        self._observers.append(observer)

    @log
    def remove_observer(self, observer):
        """
        Removes observer from our observers list.

        :param observer: the observer to remove
        :type observer: any
        """
        self._observers.remove(observer)

    @log
    def _notify_observers(self):
        """
        Tells all registered observers to update their display
        """
        for observer in self._observers:
            observer.update_according_to_app_state()


STORE = DataStore()


class Image:
    """
    Represents an image, our basic processing object.

    Image data is a numpy array. Array's data type is unspecified for now
    but we'd surely benefit from enforcing one (float32 for example) as it will
    ease the development of any later processing code

    We also store the bayer pattern the image was shot with, if applicable.

    If image is from a sensor without a bayer array, the bayer pattern must be None.
    """

    def __init__(self, data):
        """
        Constructs an Image

        :param data: the image data
        :type data: numpy.ndarray
        """
        self._data = data
        self._bayer_pattern: str = None
        self._origin: str = "UNDEFINED"
        self._destination: str = "UNDEFINED"

    def clone(self):
        """
        Clone an image

        :return: an image with global copied data
        :rtype: Image
        """
        new = Image(self.data.copy())
        new.bayer_pattern = self.bayer_pattern
        new.origin = self.origin
        new.destination = self.destination
        return new

    @property
    def destination(self):
        """
        Retrieves image destination

        :return: the destination
        :rtype: str
        """
        return self._destination

    @destination.setter
    def destination(self, destination):
        """
        Sets image destination

        :param destination: the image destination
        :type destination: str
        """
        self._destination = destination

    @property
    def data(self):
        """
        Retrieves image data

        :return: image data
        :rtype: numpy.ndarray
        """
        return self._data

    @data.setter
    def data(self, data):
        self._data = data

    @property
    def origin(self):
        """
        retrieves info on image origin.

        If Image has been read from a disk file, origin contains the file path

        :return: origin representation
        :rtype: str
        """
        return self._origin

    @origin.setter
    def origin(self, origin):
        self._origin = origin

    @property
    def bayer_pattern(self):
        """
        Retrieves the bayer pattern the image was shot with, if applicable.

        :return: the bayer pattern or None
        :rtype: str
        """
        return self._bayer_pattern

    @property
    def dimensions(self):
        """
        Retrieves image dimensions as a tuple.

        This is basically the underlying array's shape tuple, minus the color axis if image is color

        :return: the image dimensions
        :rtype: tuple
        """
        if self._data.ndim == 2:
            return self._data.shape

        dimensions = list(self.data.shape)
        dimensions.remove(min(dimensions))
        return dimensions

    @property
    def width(self):
        """
        Retrieves image width

        :return: image width in pixels
        :rtype: int
        """
        return max(self.dimensions)

    @property
    def height(self):
        """
        Retrieves image height

        :return: image height in pixels
        :rtype: int
        """
        return min(self.dimensions)

    @bayer_pattern.setter
    def bayer_pattern(self, bayer_pattern):
        self._bayer_pattern = bayer_pattern

    def needs_debayering(self):
        """
        Tells if image needs debayering

        :return: True if a bayer pattern is known and data does not have 3 dimensions
        """
        return self._bayer_pattern is not None and self.data.ndim < 3

    def is_color(self):
        """
        Tells if the image has color information

        image has color information if its data array has more than 2 dimensions

        :return: True if the image has color information, False otherwise
        :rtype: bool
        """
        return self._data.ndim > 2

    def is_bw(self):
        """
        Tells if image is black and white

        :return: True if no color info is stored in data array, False otherwise
        :rtype: bool
        """
        return self._data.ndim == 2 and self._bayer_pattern is None

    def is_same_shape_as(self, other):
        """
        Is this image's shape equal to another's ?

        :param other: other image to compare shape with
        :type other: Image

        :return: True if shapes are equal, False otherwise
        :rtype: bool
        """
        return self._data.shape == other.data.shape

    def set_color_axis_as(self, wanted_axis):
        """
        Reorganise internal data array so color information is on a specified axis

        :param wanted_axis: The 0-based number of axis we want color info to be

        Image data is modified in place
        """

        if self._data.ndim > 2:

            # find what axis are the colors on.
            # axis 0-based index is the index of the smallest data.shape item
            shape = self._data.shape
            color_axis = shape.index(min(shape))

            if color_axis != wanted_axis:
                self._data = np.moveaxis(self._data, color_axis, wanted_axis)

    def __repr__(self):
        representation = (f'{self.__class__.__name__}('
                          f'Color={self.is_color()}, '
                          f'Needs Debayer={self.needs_debayering()}, '
                          f'Bayer Pattern={self.bayer_pattern}, '
                          f'Width={self.width}, '
                          f'Height={self.height}, '
                          f'Data shape={self._data.shape}, '
                          f'Data type={self._data.dtype.name}, '
                          f'Origin={self.origin}, '
                          f'Destination={self.destination}, ')

        if self.is_color():
            representation += f"Mean R: {int(np.mean(self._data[0]))}, "
            representation += f"Mean G: {int(np.mean(self._data[1]))}, "
            representation += f"Mean B: {int(np.mean(self._data[2]))})"
        else:
            representation += f"Mean: {int(np.mean(self._data))})"

        return representation

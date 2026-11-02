from .airav import AirAV
from .avbase import AVbase
from .avsox import AVSOX
from .dmm import DMM
from .fc2 import FC2
from .javbus import JavBus
from .javdb import JavDB
from .uncensored import Caribbeancom, Heyzo, OnePondo

SOURCES = {
    source.id: source
    for source in (DMM, JavDB, JavBus, FC2, AVSOX, AirAV, OnePondo, Caribbeancom, Heyzo, AVbase)
}

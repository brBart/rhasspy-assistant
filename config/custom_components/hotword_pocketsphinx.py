"""
Provide functionality to listen for a hot/wake word from pocketsphinx.
"""
import logging
import os
import asyncio
import threading

import voluptuous as vol

from homeassistant.const import CONF_NAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers import intent, config_validation as cv

_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ['pocketsphinx==0.1.15']

DOMAIN = 'hotword_pocketsphinx'

# ------
# Config
# ------

# Path to pocketsphinx acoustic model (-hmm).
# Probably $RHASSPY_TOOLS/pocketsphinx/cmusphinx-en-us-5.2
CONF_ACOUSTIC_MODEL = 'acoustic_model'

# Path to pocketsphinx word pronunciation dictionary (-dict).
# Probably $RHASSPY_TOOLS/pocketsphinx/cmudict-en-us.dict
CONF_DICTIONARY = 'dictionary'

# Word or phrase to use for hot/wake word.
# CMU recommends this be 3-4 syllables long.
CONF_HOTWORD = 'hotword'

# Likelihood of hotword occuring (tune to lower false positive rate).
# CMU recommends this be in the range 1e-50 to 1e-5.
# Defaults to 1e-40.
CONF_THRESHOLD = 'threshold'

# Name of the audio device to record on.
#
# This string is passed directly to the Ad constructor for pocketsphinx's audio
# device (technically sphinxbase's).
#
# The format depends on whether you're using ALSA or PulseAudio, and even then
# it's incredibly confusing. You can force sphinxbase to use ALSA or PulseAudio
# by editing sphinxbase/__init__.py in your virtual environment if you're
# feeling brave.
#
# The default value of None selects the default microphone.
CONF_AUDIO_DEVICE = 'audio_device'

# Microphone sample rate (defaults to 16Khz)
CONF_SAMPLE_RATE = 'sample_rate'

# Size of recording buffer (defaults to 2048).
CONF_BUFFER_SIZE = 'buffer_size'

# ----------------------
# Configuration defaults
# ----------------------

DEFAULT_NAME = 'hotword_pocketsphinx'
DEFAULT_ACOUSTIC_MODEL = '/usr/share/pocketsphinx/model/en-us/en-us/'
DEFAULT_DICTIONARY = '/usr/share/pocketsphinx/model/en-us/cmudict-en-us.dict'
DEFAULT_THRESHOLD = 1e-40  # 1e-50 to 1e-5 recommended

DEFAULT_AUDIO_DEVICE = None
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_BUFFER_SIZE = 2048

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional(CONF_NAME, DEFAULT_NAME): cv.string,

        vol.Required(CONF_HOTWORD): cv.string,
        vol.Optional(CONF_ACOUSTIC_MODEL, DEFAULT_ACOUSTIC_MODEL): cv.string,
        vol.Optional(CONF_DICTIONARY, DEFAULT_DICTIONARY): cv.string,
        vol.Optional(CONF_THRESHOLD, DEFAULT_THRESHOLD): float,


        vol.Optional(CONF_AUDIO_DEVICE, DEFAULT_AUDIO_DEVICE): cv.string,
        vol.Optional(CONF_SAMPLE_RATE, DEFAULT_SAMPLE_RATE): int,
        vol.Optional(CONF_BUFFER_SIZE, DEFAULT_BUFFER_SIZE): int
    })
}, extra=vol.ALLOW_EXTRA)

# --------
# Services
# --------

SERVICE_LISTEN = 'listen'

# Represents the hotword detector
OBJECT_DECODER = '%s.decoder' % DOMAIN

# Not doing anything
STATE_IDLE = 'idle'

# Listening for the hotword
STATE_LISTENING = 'listening'

# Fired when the hotword is detected
EVENT_HOTWORD_DETECTED = 'hotword_detected'

# -----------------------------------------------------------------------------
@asyncio.coroutine
def async_setup(hass, config):
    name = config[DOMAIN].get(CONF_NAME, DEFAULT_NAME)
    hotword = config[DOMAIN].get(CONF_HOTWORD)
    acoustic_model = os.path.expanduser(config[DOMAIN].get(CONF_ACOUSTIC_MODEL, DEFAULT_ACOUSTIC_MODEL))
    dictionary = os.path.expanduser(config[DOMAIN].get(CONF_DICTIONARY, DEFAULT_DICTIONARY))
    threshold = config[DOMAIN].get(CONF_THRESHOLD, DEFAULT_THRESHOLD)

    audio_device_str = config[DOMAIN].get(CONF_AUDIO_DEVICE, DEFAULT_AUDIO_DEVICE)
    sample_rate = config[DOMAIN].get(CONF_SAMPLE_RATE, DEFAULT_SAMPLE_RATE)
    buffer_size = config[DOMAIN].get(CONF_BUFFER_SIZE, DEFAULT_BUFFER_SIZE)

    detected_event = threading.Event()
    detected_phrase = None
    terminated = False

    from pocketsphinx import Pocketsphinx, Ad
    decoder = Pocketsphinx(
        hmm=acoustic_model,
        lm=False,
        dic=dictionary,
        keyphrase=hotword,
        kws_threshold=threshold)

    audio_device = Ad(audio_device_str, sample_rate)

    state_attrs = {
        'friendly_name': 'Hotword',
        'icon': 'mdi:microphone'
    }

    @asyncio.coroutine
    def async_listen(call):
        nonlocal terminated, detected_phrase
        terminated = False
        detected_phrase = None

        hass.states.async_set(OBJECT_DECODER, STATE_LISTENING, state_attrs)

        def listen():
            buf = bytearray(buffer_size)

            with audio_device:
                with decoder.start_utterance():
                    while not terminated and audio_device.readinto(buf) >= 0:
                        decoder.process_raw(buf, False, False)
                        hyp = decoder.hyp()
                        if hyp:
                            with decoder.end_utterance():
                                # Make sure the hotword is matched
                                detected_phrase = hyp.hypstr
                                if detected_phrase == hotword:
                                    break

            detected_event.set()

        # Listen asynchronously
        detected_event.clear()
        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        yield from asyncio.get_event_loop().run_in_executor(None, detected_event.wait)

        if not terminated:
            thread.join()
            hass.states.async_set(OBJECT_DECODER, STATE_IDLE, state_attrs)

            # Fire detected event
            hass.bus.async_fire(EVENT_HOTWORD_DETECTED, {
                'name': name  # name of the component
            })

    hass.services.async_register(DOMAIN, SERVICE_LISTEN, async_listen)
    hass.states.async_set(OBJECT_DECODER, STATE_IDLE, state_attrs)

    # Make sure snowboy terminates property when home assistant stops
    @asyncio.coroutine
    def async_terminate(event):
        nonlocal terminated
        terminated = True
        detected_event.set()

    hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, async_terminate)

    _LOGGER.info('Started')

    return True

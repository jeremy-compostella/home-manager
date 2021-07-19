#!/usr/bin/env python3
#
# Author: Jeremy Compostella <jeremy.compostella@gmail.com>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer
#      in the documentation and/or other materials provided with the
#      distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.

import configparser
import threading

from consumer import *
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from sensor import *
from stat_sensor import MyEmail

reader = None
class SensorReader:
    expiration = None
    usage = None

    def __init__(self, vue):
        self.vue = vue
        self._usage_lock = threading.Lock()

    def __read(self):
        try:
            return vue.read(scale=Scale.SECOND.value)
        except:
            return False

    def read(self):
        with self._usage_lock:
            if self.expiration and datetime.now() < self.expiration:
                return self.usage
            usage = test(lambda: self.__read(),
                         "Emporia is unreachable", end_msg = "Emporia is back")
            expiration = datetime.now() + timedelta(seconds=15)
        return self.usage

def test(fun, msg, end_msg=None, timeout=90, sleep=15):
    start = datetime.now()
    while True:
        ret = fun()
        if ret:
            if not start and end_msg:
                mailer.sendMIMEText(end_msg)
            return ret
        if start and start + timedelta(seconds=timeout) < datetime.now():
            mailer.sendMIMEText(msg)
            start = None
            time.sleep(sleep)

def hvac_yellow():
    print('%s' % __name__)
    usage = reader.read()
    return usage['A/C'] > .1 and usage['air handler'] < .1,

def hvac_red():
    """HVAC: When the Yellow wire is shunt by float T-switch the air
handler stops running but the Heat Pump is still running
    """
    print('%s' % __name__)
    usage = reader.read();
    return usage['A/C'] > .1 and usage['air handler'] < .1

def main():
    config = configparser.ConfigParser()
    config.read("home.ini")

    global mailer
    mailer = MyEmail(config['Email'])
    vue = MyVue2(config['Emporia'])
    global reader
    reader = SensorReader(vue)
    threads = [ threading.Thread(target=lambda: test(hvac_yellow,
                                                     'air handler running alone'))
                # threading.Thread(target=hvac_red)
               ]
    for t in threads:
        t.start()

    # IP address monitoring
    while True:
        time.sleep(60)
        # # Power sensor
        # # ------------
        # # Sometimes the Emporia service becomes inaccessible.
        # usage = test(lambda: emporia(vue), "Emporia is unreachable",
        #              end_msg = "Emporia is back")

        # # HVAC
        # # ----
        # # 1. When the Yellow wire is shunt by float T-switch the air handler
        # #    stops running but the Heat Pump is still running
        # test(lambda: usage['A/C'] > .1 and usage['air handler'] < .1,
        #      "air handler is not running while A/C is")
        # # 2. When the Red wire is shunt by float T-switch, the air
        # #    handler keeps running but the condensation and the heat
        # #    pump do not run
        # test(lambda: usage['A/C'] < .1 and usage['air handler'] > .1,
        #      "air handler is not running while A/C is")
        # print("HVAC is okay")

        # # Pool
        # # ----
        # # 1. Out of range power consumption (June 21 2021: pool filter
        # #    head cracked)
        # # 2. Ran for a very short period of time (less than 2 minutes)
        # #    July 7 2021: less than 2 minutes: pool pump capacitor
        # #    died)
        # time.sleep(60)

if __name__ == "__main__":
    main()

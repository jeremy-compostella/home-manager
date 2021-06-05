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

from consumer import *
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from sensor import *
from stat_sensor import MyEmail

config = None

def alert(title):
    msg = MIMEMultipart('related')
    msg.preamble = 'This is a multi-part message in MIME format.'
    msg['Subject'] = title
    alternative = MIMEMultipart('alternative')
    msg.attach(alternative)
    alternative.attach(MIMEText(title.encode('utf-8'), 'plain', _charset='utf-8'))
    MyEmail(config['Email']).send(msg)

def main():
    global config
    config = configparser.ConfigParser()
    config.read("home.ini")

    vue = MyVue2(config['Emporia'])
    while True:
        # Monitor Emporia service
        entered_at = datetime.now()
        while True:
            try:
                usage=vue.read(scale=Scale.SECOND.value)
                break
            except:
                if entered_at and entered_at + timedelta(seconds=90) < datetime.now():
                    alert("Emporia has been inaccessible for more than 90 seconds")
                    entered_at = None
                    time.sleep(15)
                continue
        if not entered_at:
            alert("Emporia service is back")

        # Monitor Air Handler
        if usage['A/C'] > 1 and usage['air handler'] < .1:
            entered_at = datetime.now()
            while True:
                sleep(15)
                usage=vue.read(scale=Scale.SECOND.value)
                if usage['A/C'] < 1 or usage['air handler'] > .1:
                    break
                if entered_at and entered_at + timedelta(seconds=90) < datetime.now():
                    alert('air handler is not running while A/C is')
                    entered_at = None

if __name__ == "__main__":
    main()

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

import argparse
import csv
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import re
import sys

from consumer import *
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from os.path import basename, splitext, isfile
from producer import *
from statistics import median
from tools import *

class SensorLogReader(csv.DictReader):
    DEFAULT_FILENAME="sensor.csv"
    date = None

    def __init__(self, date=None, filename=None):
        if not filename:
            if date:
                filename = self.DEFAULT_FILENAME + "." + date.strftime("%Y%m%d")
            else:
                filename = self.DEFAULT_FILENAME
        self.filename = filename
        if not isfile(filename):
            raise FileNotFoundError()

    def __iter__(self):
        f = open(self.filename, 'r')
        csv.DictReader.__init__(self, f)
        return self

    def __next__(self):
        d = csv.DictReader.__next__(self)
        for key, value in d.items():
            if key == 'time':
                d[key] = datetime.strptime(value, "%m/%d/%Y %H:%M:%S")
                if not self.date:
                    self.date= d[key]
            else:
                try:
                    d[key] = float(value)
                except ValueError:
                    d[key] = value
        return d

def get_sensors_and_label(val, items):
    sensors=[]
    labels=[]
    for item in items:
        for sensor in item.sensors:
            sensors.append([ abs(x) for x in val[sensor] ])
            description = item.description
            if len(item.sensors) > 1:
                description += ' (' + sensor + ')'
            labels.append(description)
    return sensors, labels

def plot(reader, title, consumers, producers, filename=None):
    val = { }
    for current in iter(reader):
        for key, value in current.items():
            if not key in val:
                val[key] = [ value ]
            else:
                val[key].append(value)

    fig, ax = plt.subplots()
    ax.stackplot(val['time'], [ x - y for (x, y) in zip(val['net'], val['solar']) ],
                 labels=["Other"],
                 colors=['lightgrey', "tab:blue", "gold", "tab:cyan", "tab:pink",
                         "tab:red", "tab:green", "tab:orange"])

    (sensors, labels) = get_sensors_and_label(val, consumers)
    ax.stackplot(val['time'], sensors, labels=labels)
    (sensors, labels) = get_sensors_and_label(val, producers)
    for (s, l) in zip(sensors, labels):
        ax.plot(val['time'], s, color='black', label=l, lw=.8)

    ax.legend(loc='upper left', title=r"$\bf{Producers}$ and $\bf{Consumers}$")
    plt.grid(which='major', linestyle='dotted')
    ax.set(xlabel="Time",
           ylabel="Power (KW)",
           title=title)

    ax2 = ax.twinx()
    ax2.plot(val['time'], val['outdoor temp'], label='Outdoor', lw=0.8,
             color='lightcoral')
    ax2.plot(val['time'],
             [ (x + y) / 2 for (x, y) in zip(val['Home'], val['Living Room']) ],
             label='Indoor', lw=0.8, color='blueviolet')
    ax2.plot(val['time'], val['Bedroom'], label='Bedroom', lw=0.8, color='orange')

    ax2.legend(loc='upper right', title=r'$\bf{Temperatures}$')

    ax2.set(ylabel="Temperature (°F)")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax2.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    plt.xlim([val['time'][0], val['time'][-1]])

    plt.tight_layout()
    fig.set_size_inches(18.5, 8)
    if filename:
        plt.savefig(filename)
    else:
        plt.show()

def parse_args():
    parser = argparse.ArgumentParser(description='Process FILE and compute a report.')
    parser.add_argument('--file', dest='file', help='Data file')
    parser.add_argument('--files', dest='prefix',
                        help='Build report for files prefixed by PREFIX')
    parser.add_argument('--plot', dest='plot', action='store_true',
                        help='Plot the computed data')
    parser.add_argument('--plot-to-file', dest='plot_filename',
                        help='Plot the computed data and save to FILE')
    parser.add_argument('--send-email', dest='send_email', action='store_true',
                        help='send the report')
    return parser.parse_args()

def duration_string(duration):
    if math.floor(duration / 60) == 0:
        return "%dmin" % duration
    return "%dh%02dmin" % (duration / 60, duration % 60)

def compute_for(filename, utility, producers, consumers):
    sums = { k: 0 for k in ['imported', 'exported', 'produced', 'onpeak', 'offpeak' ] }
    res = { k: { 'sum':0, 'max':0, 'time':0 } for k in producers + consumers }
    temps = [ ]

    reader = SensorLogReader(filename=filename)
    utility.loadRate(next(iter(reader))['time'])
    for current in iter(reader):
        temps.append(current['outdoor temp'])

        date = current["time"]

        net = current["net"] / 60
        if net > 0:
            sums['imported'] += net
            if utility.isOnPeak(date):
                sums['onpeak'] += net
            else:
                sums['offpeak'] += net
        else:
            sums['exported'] += abs(net)

        for item in producers + consumers:
            total = item.totalPower(current)
            if total >= 0.1:
                res[item]['time'] += 1
            res[item]['sum'] += total / 60
            res[item]['max'] = max(total, res[item]['max'])
    return sums, res, temps, reader.date

def build_report(sums, res, temps, utility, producers, consumers):
    total = sum([ res[p]['sum'] for p in producers ]) - \
        sums['exported'] + sums['imported']

    report = "Temperature: Min %.1f°F, Max %.1f°F, Median %.1f°F\n" % \
        (min(temps), max(temps), median(temps))
    report += "\n"
    report += "Summary:\n"
    report += "- Total consumption: %.2f KWh - %.2f KWh (%d%%) from local production\n" % \
        (total, total - sums['imported'],
         (total - sums['imported']) / total * 100)
    report +="- Imported: %.2f KWh (%d%%), Exported: %.2f KWh\n" % \
        (sums['imported'], sums['imported']/total*100, sums['exported'])
    if sums['onpeak'] != 0:
        report += "- On Peak: %.2f KWh (%d%%), Off Peak: %.02f KWh\n" % \
            (sums['onpeak'], (sums['onpeak'] / sums['imported']) * 100, \
             sums['offpeak'])
    cost = ((sums['offpeak'] * utility.rate["offpeak"]) +
            (sums['onpeak'] * utility.rate["onpeak"]) -
            (sums['exported'] * utility.rate["export"]))
    report += "- Cost: %.2f USD (%.2f USD saved compared to EZ-3)\n" % \
        (cost, ((total - sums['onpeak']) * .0829 + \
                (sums['onpeak'] * .2895)) - cost)
    report += "\n"

    report += "Producer(s):\n"
    for p in producers:
        report += "- %s: %.2f KWh (%d%%) - Max %.2f KW - %s\n" % \
            (p.description, res[p]['sum'], \
             (res[p]['sum'] / total) * 100, res[p]['max'],
             duration_string(res[p]['time']))
    report += '\n'

    report += "Consumer(s):\n"
    for c in list(sorted(consumers, key=lambda item: res[item]['sum'], reverse=True)):
        if res[c]['sum'] < 0.01:
            continue
        report += "- %s: %.2f KWh (%d%%) - Max %.2f KW - %s\n" % \
            (c.description, res[c]['sum'], \
             (res[c]['sum'] / total) * 100, res[c]['max'],
             duration_string(res[c]['time']))
    return report

def main(argv):
    args = parse_args()

    config = init()

    utility = get_utility()
    producers=[ Producer(config[x]) for x in config['general']['producers'].split(',') ]
    consumers=[ Consumer(config[x]) for x in config['general']['consumers'].split(',') ]

    sums = res = temps = first = last = date = None
    if args.prefix:
        pattern = re.compile("^%s.*$" % args.prefix)
        for filename in os.listdir("."):
            if not pattern.search(filename):
                continue
            s, r, t, d = compute_for(filename, utility, producers, consumers)
            if not sums:
                sums, res, temps, date = s, r, t, d
                first = last = d
            else:
                sums = { k:v + sums[k] for k, v in s.items() }
                res = { k:{ 'time':v['time'] + res[k]['time'],
                            'sum':v['sum'] + res[k]['sum'],
                            'max':max(v['max'], res[k]['max']) }
                        for k, v in r.items() }
                temps += t
                first = min(d, first)
                last = max(d, last)
        title = "Report for %s - %s" % (first.strftime("%A %B %d %Y"),
                                                last.strftime("%A %B %d %Y"))
    else:
        sums, res, temps, date = compute_for(args.file, utility, producers, consumers)
        title = "Daily report for " + date.strftime("%A %B %d %Y")

    report = build_report(sums, res, temps, utility, producers, consumers)
    title = title.lstrip("0").replace(" 0", " ")
    if args.plot:
        plot(SensorLogReader(filename=args.file), title, consumers, producers)
    elif args.plot_filename:
        plot(SensorLogReader(filename=args.file), title, consumers, producers,
             filename=args.plot_filename)
    elif args.send_email:
        msg = MIMEMultipart('mixed')
        msg.preamble = 'This is a multi-part message in MIME format.'
        msg['Subject'] = title

        related = MIMEMultipart('related')
        msg.attach(related)

        alternative = MIMEMultipart('alternative')
        related.attach(alternative)
        alternative.attach(MIMEText(report.encode('utf-8'), 'plain', _charset='utf-8'))

        if not args.prefix:
            reader = SensorLogReader(filename=args.file)
            plot_file = reader.filename.replace(".csv", "") + ".pdf"
            plot(reader, title, consumers, producers, filename=plot_file)
            with open(plot_file, "rb") as f:
                part = MIMEApplication(f.read(), Name=basename(plot_file))
            part['Content-Disposition'] = 'attachment; filename="%s"' % \
                basename(plot_file)
            msg.attach(part)

        sendEmail(msg)
    else:
        print(title)
        print("")
        print(report)

if __name__ == "__main__":
    main(sys.argv[1:])

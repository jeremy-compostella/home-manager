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
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import re
import sys

from consumer import *
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from os.path import basename
from producer import *
from statistics import median
from tools import *

def get_sensors_and_label(val, items):
    sensors=[]
    labels=[]
    for item in items:
        if item.description == 'Other':
            continue
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
    if math.floor(duration / (24 * 60)) == 0:
        return "%dh%02dmin" % (duration / 60, duration % 60)
    days = duration / (24 * 60)
    duration -= floor(days) * 24 * 60
    return "%dd%dh%02dmin" % (days, duration / 60, duration % 60)

def compute_for(filename, utility, producers, consumers):
    sums = { k: 0 for k in ['imported', 'exported', 'produced', 'onpeak', 'offpeak' ] }
    res = { k: { 'sum':0, 'max':0, 'time':0 } for k in producers + consumers }
    sensor = { 'outdoor temp':[], 'humidity':[],
               'Home':[], 'Pool thermometer':[],
               'EV SoC':[], 'EV mileage':[] }

    reader = SensorLogReader(filename=filename)
    utility.loadRate(next(iter(reader))['time'])
    for current in iter(reader):
        for key in sensor.keys():
            if key in current:
                sensor[key].append(current[key])

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
    return sums, res, sensor, reader.date

def build_report(sums, res, sensor, utility, producers, consumers):
    total = sum([ res[p]['sum'] for p in producers ]) - \
        sums['exported'] + sums['imported']

    report = "Temperature(s):\n"
    if 'outdoor temp' in sensor:
        report += "- Outdoor: Min %.1f°F, Max %.1f°F, Median %.1f°F\n" % \
            (min(sensor['outdoor temp']), max(sensor['outdoor temp']),
             median(sensor['outdoor temp']))
    if 'humidity' in sensor and sensor['humidity']:
        report += "- Humidity: Min %d%%, Max %d%%, Median %.1f%%\n" % \
            (min(sensor['humidity']), max(sensor['humidity']),
             median(sensor['humidity']))
    if 'Home' in sensor:
        report += "- Indoor: Min %.1f°F, Max %.1f°F, Median %.1f°F\n" % \
            (min(sensor['Home']), max(sensor['Home']), median(sensor['Home']))
    if 'Pool thermometer' in sensor:
        pool = [ x for x in sensor['Pool thermometer'] if type(x) == float ]
        report += "- Pool: Min %.1f°F, Max %.1f°F, Median %.1f°F\n" % \
            (min(pool), max(pool), median(pool))

    if 'EV SoC' in sensor:
        sensor['EV SoC'] = [ x for x in sensor['EV SoC'] if x != -1 ]
        if len(sensor['EV SoC']) > 0:
            report += "\n"
            report += 'Car:\n'
            report += '- State of Charge: Min %.1f%%, Max %.1f%%, Latest %.1f%%\n' % \
                (min(sensor['EV SoC']), max(sensor['EV SoC']), sensor['EV SoC'][-1])
    if 'EV mileage' in sensor and sensor['EV mileage']:
        report += '- Mileage: %.1f miles, +%.1f miles\n' % \
            (sensor['EV mileage'][-1],
             sensor['EV mileage'][-1] - sensor['EV mileage'][0])
    report += "\n"
    report += "Summary:\n"
    from_producers = total - sums['imported']
    report += "- Total consumption: %.2f KWh - %.2f KWh (%.1f%%) from local production\n" % \
        (total, from_producers, from_producers / total * 100)
    report +="- Imported: %.2f KWh (%.1f%%) - Exported: %.2f KWh\n" % \
        (sums['imported'], sums['imported']/total*100, sums['exported'])
    if sums['onpeak'] > 0.01:
        report += "- On Peak: %.2f KWh (%.1f%%), Off Peak: %.02f KWh\n" % \
            (sums['onpeak'], (sums['onpeak'] / sums['imported']) * 100, \
             sums['offpeak'])
    cost = ((sums['offpeak'] * utility.rate["offpeak"]) +
            (sums['onpeak'] * utility.rate["onpeak"]) -
            (sums['exported'] * utility.rate["export"]))
    report += "- Cost: %.2f USD\n" % cost
    report += "\n"

    report += "Producer(s): %.1f%% used\n" % \
        (from_producers / sum([ res[p]['sum'] for p in producers]) * 100)
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
        report += "- %s: %.2f KWh (%.1f%%) - Max %.2f KW - %s\n" % \
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
    consumers.append(Other(consumers))

    sums = res = temps = first = last = date = None
    if args.prefix:
        pattern = re.compile("^%s.*$" % args.prefix)
        for filename in sorted(os.listdir(".")):
            if not pattern.search(filename):
                continue
            s, r, t, d = compute_for(filename, utility, producers, consumers)
            if not sums:
                sums, res, sensor, date = s, r, t, d
                first = last = d
            else:
                sums = { k:v + sums[k] for k, v in s.items() }
                res = { k:{ 'time':v['time'] + res[k]['time'],
                            'sum':v['sum'] + res[k]['sum'],
                            'max':max(v['max'], res[k]['max']) }
                        for k, v in r.items() }
                for k, v in sensor.items():
                    sensor[k] += t[k]
                first = min(d, first)
                last = max(d, last)
        title = "Report for %s - %s" % (first.strftime("%A %B %d %Y"),
                                                last.strftime("%A %B %d %Y"))
    else:
        sums, res, sensor, date = compute_for(args.file, utility, producers, consumers)
        title = "Daily report for " + date.strftime("%A %B %d %Y")

    report = build_report(sums, res, sensor, utility, producers, consumers)
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

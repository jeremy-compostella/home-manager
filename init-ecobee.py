import configparser

import shelve
from datetime import datetime

import pytz
from six.moves import input

from pyecobee import *

logger = logging.getLogger(__name__)
config = configparser.ConfigParser()

def persist_to_shelf(file_name, ecobee):
    db = shelve.open(file_name, protocol=2)
    db[ecobee.thermostat_name] = ecobee
    db.close()

def refresh_tokens(ecobee):
    token_response = ecobee.refresh_tokens()
    print('TokenResponse returned from ecobee.refresh_tokens():\n{0}'.format(token_response.pretty_format()))
    persist_to_shelf(config["Ecobee"]["shelve_db"], ecobee)

def request_tokens(ecobee):
    token_response = ecobee.request_tokens()
    print('TokenResponse returned from ecobee.request_tokens():\n{0}'.format(token_response.pretty_format()))
    persist_to_shelf(config["Ecobee"]["shelve_db"], ecobee)

def authorize(ecobee):
    authorize_response = ecobee.authorize()
    print('AutorizeResponse returned from ecobee.authorize():\n{0}'.format(authorize_response.pretty_format()))
    persist_to_shelf(config["Ecobee"]["shelve_db"], ecobee)
    print('Please goto ecobee.com, login to the web portal and click on the\
settings tab. Ensure the My ' 'Apps widget is enabled. If it is not\
click on the My Apps option in the menu on the left. In the ' 'My Apps\
widget paste "{0}" and in the textbox labelled "Enter your 4 digit pin\
to ' 'install your third party app" and then click "Install App". The\
next screen will display any ' 'permissions the app requires and will\
ask you to click "Authorize" to add the application.\n\n' 'After\
completing this step please hit "Enter" to\
continue.'.format(authorize_response.ecobee_pin))
    input()

if __name__ == '__main__':
    config.read("home.ini")

    try:
        db = shelve.open(config["Ecobee"]["shelve_db"], protocol=2)
        ecobee = db[config["Ecobee"]["name"]]
    except KeyError:
        ecobee = EcobeeService(thermostat_name=config["Ecobee"]["name"],
                               application_key=config["Ecobee"]["key"])
    finally:
        db.close()

    if ecobee.authorization_token is None:
        authorize(ecobee)

    if ecobee.access_token is None:
        request_tokens(ecobee)

    now_utc = datetime.now(pytz.utc)
    if now_utc > ecobee.refresh_token_expires_on:
        authorize(ecobee)
        request_tokens(ecobee)
    elif now_utc > ecobee.access_token_expires_on:
        token_response = refresh_tokens(ecobee)

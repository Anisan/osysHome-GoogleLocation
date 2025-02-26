import datetime
import time
import os
import hashlib
import requests
from requests import Session
import json
import math
from flask import redirect
from sqlalchemy import delete
from app.core.main.BasePlugin import BasePlugin
from app.database import session_scope
from plugins.GoogleLocation.models import Location
from plugins.GoogleLocation.forms.SettingsForm import SettingsForm
from plugins.GoogleLocation.forms.LocationForm import LocationForm
from app.core.lib.cache import saveToCache, getFilesCache, findInCache
from app.core.lib.common import addNotify, callPluginFunction
from app.core.lib.constants import CategoryNotify

LANGUAGE = "ru"

class GoogleLocation(BasePlugin):

    def __init__(self,app):
        super().__init__(app,__name__)
        self.title = "GoogleLocation"
        self.description = """Get location from Google Location sharing"""
        self.category = "App"
        self.version = "0.1"
        self.actions = ["cycle"]

    def initialization(self):
        pass

    def admin(self, request):

        op = request.args.get("op", None)
        if op == "upload_cookie":
            file = request.files['file']
            if file.filename == '':
                return redirect(self.name)
            if file:
                file_content = file.read()
                saveToCache(file.filename, file_content, os.path.join(self.name,"cookies"))
                return redirect(self.name)
        if op == "delete_cookie":
            name = request.args.get("name", None)
            full_path = findInCache(name,os.path.join(self.name,"cookies"))
            os.remove(full_path)
            return redirect(self.name)
        
        if op == 'update_location':
            self.update_location()
            return redirect(self.name)
        
        if op == "user_edit":
            id = int(request.args.get("id"))
            with session_scope() as session:
                location = session.get(Location, id)
                form = LocationForm(obj=location)
                if form.validate_on_submit():
                    form.populate_obj(location)
                    session.commit()
                    return redirect(self.name)
            return self.render("googlelocation_user.html", {"form": form})
        
        if op == "user_delete":
            id = int(request.args.get("id"))
            with session_scope() as session:
                sql = delete(Location).where(Location.id == id)
                session.execute(sql)
                session.commit()
            return redirect(self.name)



        settings = SettingsForm()
        if request.method == 'GET':
            settings.timeout.data = self.config.get('timeout',1)
            settings.limit_speed_min.data = self.config.get('limit_speed_min',1)
            settings.limit_speed_max.data = self.config.get('limit_speed_max',150)
        else:
            if settings.validate_on_submit():
                self.config["timeout"] = settings.timeout.data
                self.config["limit_speed_min"] = settings.limit_speed_min.data
                self.config["limit_speed_max"] = settings.limit_speed_max.data
                self.saveConfig()
                return redirect(self.name)
        
        locations = Location.query.all()

        files = getFilesCache(os.path.join(self.name,"cookies"))
        cookie_files = [{'name':file, 'error': self.config.get("error_"+file,None)} for file in files]

        content = {
            'locations': locations,
            'form': settings,
            'cookie_files':cookie_files,
            'last_update':self.config.get('last_update','???')
        }
        return self.render('googlelocation_main.html', content)
        
    def cyclic_task(self):
        self.update_location()
        timeout = self.config.get("timeout", 10)
        self.event.wait(float(timeout))

    def update_location(self):
        locations = []
        cookies = getFilesCache(os.path.join(self.name,"cookies"))
    
        for cookie in cookies:
            locations.extend(self.get_location(cookie))
        
        with session_scope() as session:
            for location in locations:
                rec = session.query(Location).where(Location.id_user == location["id"]).one_or_none()

                if not rec:
                    rec = Location()
                    rec.id_user = location["id"]
                    rec.name = location["name"]
                    rec.fullname = location["fullname"]
                    rec.image = location['image']
                    rec.speed = 0
                    rec.last_update = datetime.datetime.now()
                    session.add(rec)
                    addNotify('Внимание!',f'Добавлен новый пользователь {rec.fullname}!',CategoryNotify.Info, self.name)
                
                if location['lat'] == 0 and location['lon'] == 0:
                    if time.time() - rec.last_update.timestamp() > 24*60*60:
                        addNotify('Внимание!',f'Некорректные данные пользователя {rec.fullname}, проверьте настройки!',CategoryNotify.Warning, self.name)
                    continue

                if rec.last_update.strftime('%Y-%m-%d %H:%M:%S') == datetime.datetime.fromtimestamp(int(location['timestamp'])/1000).strftime('%Y-%m-%d %H:%M:%S'):
                    if time.time() - rec.last_update.timestamp() > 24*60*60:
                        addNotify('Внимание!',f'Данные пользователя {rec.fullname} не обновляются, проверьте настройки!',CategoryNotify.Warning, self.name)
                    continue
                
                rec.last_update = datetime.datetime.fromtimestamp(location['timestamp']/1000)
                location['speed'] = self.get_speed(rec, location)
                if self.config['limit_speed_min'] > location['speed']:
                    location['speed'] = 0
                if self.config['limit_speed_max'] > abs(location['speed'] - rec.speed):
                    rec.speed = location['speed']

                rec.address = location['address']
                rec.lat = location['lat']
                rec.lng = location['lon']
                rec.accuracy = location['accuracy']
                rec.battery_level = location.get('battery', rec.battery_level)
                rec.battery_charging = location['charging']

                if rec.sendtogps:
                    args = {
                        'device': rec.id_user,
                        'lat': rec.lat,
                        'lon': rec.lng,
                        'accuracy': rec.accuracy,
                        'address': rec.address,
                        'speed': rec.speed,
                        'battery': rec.battery_level,
                        'charging': rec.battery_charging,
                        'provider': self.name,
                    }
                    callPluginFunction("GpsTracker","addGpsPosition",args)

            session.commit()
                
        self.config['last_update'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.saveConfig()

    def get_speed(self, last: Location, new):
        time_last = last.last_update
        time_new = new['timestamp'] / 1000
        if not last.lat or not last.lng:
            return 0
        dist = self.calculate_the_distance(last.lat, last.lng, new['lat'], new['lon'])
        diff = time_new - time_last.timestamp()
        if diff == 0:
            return 0
        return round(dist / diff * 3.6, 2)  # km/h

    def calculate_the_distance(self, latA, lonA, latB, lonB):
        EARTH_RADIUS = 6372795
        
        lat1 = math.radians(latA)
        lat2 = math.radians(latB)
        long1 = math.radians(lonA)
        long2 = math.radians(lonB)
        
        cl1 = math.cos(lat1)
        cl2 = math.cos(lat2)
        sl1 = math.sin(lat1)
        sl2 = math.sin(lat2)
        
        delta = long2 - long1
        cdelta = math.cos(delta)
        sdelta = math.sin(delta)
        
        y = math.sqrt((cl2 * sdelta) ** 2 + (cl1 * sl2 - sl1 * cl2 * cdelta) ** 2)
        x = sl1 * sl2 + cl1 * cl2 * cdelta
        
        ad = math.atan2(y, x)
        
        dist = round(ad * EARTH_RADIUS)
        return dist

    def crc32(self, data):
        return hashlib.md5(data).hexdigest()

    def get_location(self, cookie_file):
        full_path = findInCache(cookie_file,os.path.join(self.name,"cookies"))
        try:
            result = self.get_location_data(full_path)
        except Exception as e:
            self.logger.exception(e)
            self.config[f'error_{cookie_file}'] = str(e)
            return []
        
        self.config[f'error_{cookie_file}'] = ''
        return_data = []
        
        if result[9]:
            return_data.append({
                'id': self.crc32(cookie_file.encode('utf-8')),
                'name': cookie_file,
                'fullname': cookie_file,
                'image': '',
                'address': result[9][1][4],
                'timestamp': result[9][1][2],
                'lat': result[9][1][1][2],
                'lon': result[9][1][1][1],
                'accuracy': result[9][1][3],
                'battery': 0,
                'charging': 0,
            })
        
        if result[0]:
            for user in result[0]:
                return_data.append({
                    'id': user[6][0],
                    'name': user[6][3],
                    'fullname': user[6][2],
                    'image': user[0][1],
                    'address': user[1][4] if user[1] else '',
                    'timestamp': user[1][2] if user[1] else '',
                    'lat': user[1][1][2] if user[1] else 0,
                    'lon': user[1][1][1] if user[1] else 0,
                    'accuracy': user[1][3] if user[1] else '',
                    'battery': user[13][1] if user[13] and len(user[13])>1 else None,
                    'charging': user[13][0] if user[13] and len(user[13])>0 else None,
                })
        
        self.logger.debug(return_data)
        return return_data
    
    def is_json(self, myjson):
        try:
            json.loads(myjson)
        except ValueError:
            return False
        return True
    
    def parseCookieFile(self,cookiefile):
        """Parse a cookies.txt file and return a dictionary of key value pairs
        compatible with requests."""
        import re
        cookies = {}
        with open(cookiefile, 'r') as fp:
            for line in fp:
                if not re.match(r'^\#', line):
                    lineFields = re.findall(r'[^\s]+', line) #capturing anything but empty space
                    try:
                        cookies[lineFields[5]] = lineFields[6]
                    except Exception as e:
                        print(e)
            
        return cookies

    def _load_text_cookies(self, session, cookies_file):
        try:
            text = cookies_file.read()#.decode('utf-8')
            cookies = []
            for line in text.splitlines():
                if not line.strip().startswith('#') and line:
                    cookie = Cookie(*line.strip().split())
                    cookies.append(cookie)
            for cookie in cookies:
                session.cookies.set(**cookie.to_dict())
        except Exception as ex:
            self.logger.exception(ex)
            message = 'Could not properly load cookie text file.'
            raise InvalidCookies(message)
        return session
    
    def get_location_data(self, cookie_file):
        url = 'https://www.google.com/maps/rpc/locationsharing/read?authuser=0&hl=' + LANGUAGE + '&gl=' + LANGUAGE + '&pb='
        url += '!1m7!8m6!1m3!1i14!2i8413!3i5385!2i6!3x4095!2m3!1e0!2sm!3i407105169!3m7!2sen!5e1105!12m4!1e68!2m2!1sset!2sRoadmap!4e1!5m4!1e4!8m2!1e0!1e1!6m9!1e12!2i2!26m1!4b1!30m1!1f1.3953487873077393!39b1!44e1!50e0!23i4111425'
    
        
        #with open(cookie_file, 'r') as fp:
        #    data = fp.readline().strip()

        #headers = {}
        #if data.startswith('#'):
        #    cookies = self.parseCookieFile(cookie_file)
        #else:
        #    headers = {'Cookie': data}

        #response = requests.get(url, headers=headers, cookies=cookies, timeout=30)

        session = Session()

        with open(cookie_file, 'r') as fp:
            session = self._load_text_cookies(session, fp)

        payload = {'authuser': 0,
                   'hl': 'en',
                   'gl': 'us',
                   # pd holds the information about the rendering of the map and
                   # it is irrelevant with the location sharing capabilities.
                   # the below info points to google's headquarters.
                   'pb': ('!1m7!8m6!1m3!1i14!2i8413!3i5385!2i6!3x4095'
                          '!2m3!1e0!2sm!3i407105169!3m7!2sen!5e1105!12m4'
                          '!1e68!2m2!1sset!2sRoadmap!4e1!5m4!1e4!8m2!1e0!'
                          '1e1!6m9!1e12!2i2!26m1!4b1!30m1!'
                          '1f1.3953487873077393!39b1!44e1!50e0!23i4111425')}
        url = 'https://www.google.com/maps/rpc/locationsharing/read'
        response = session.get(url, params=payload, verify=True)
        
        if response.status_code != 200:
            raise Exception(f'Error connection: {response.status_code} => {response.headers}')
        
        result = response.content[4:]
        
        if not self.is_json(result):
            raise Exception(f'Not valid json result: {result}')
        
        result = json.loads(result)
        
        if not result[0] and len(result) < 10:
            #if hasattr(self, 'sendnotification'):
            #    self.sendnotification('Проблема с cookie файлом!', 'danger')
            raise Exception(f'Error json data: {json.dumps(result)}')
        
        #self.logger.debug(result)
        return result
    
class InvalidCookies(Exception):
    """The cookies provided do not provide a valid session."""

from dataclasses import dataclass
@dataclass
class Cookie:
    """Models a cookie."""

    domain: str
    flag: bool
    path: str
    secure: bool
    expiry: int
    name: str
    value: str

    def to_dict(self):
        """Returns the cookie as a dictionary.

        Returns:
            cookie (dict): The dictionary with the required values of the cookie

        """
        return {key: getattr(self, key) for key in ('domain', 'name', 'value', 'path')}
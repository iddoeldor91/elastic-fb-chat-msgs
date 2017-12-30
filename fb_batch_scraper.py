# -*- coding: UTF-8 -*-

import logging
import datetime
import requests
import json
import argparse
from random import choice, random
from time import time, sleep
from bs4 import BeautifulSoup as bs
from elasticsearch import Elasticsearch

# ######## Arguments handler #########
# run via ./fb_batch.py "username@gmail.com" password
parser = argparse.ArgumentParser(description='Batch scraper')
parser.add_argument('username', action="store")
parser.add_argument('password', action="store")
args = parser.parse_args()
# ####################################

# ######## Constants #########
ELASTIC_INDEX_ID = 'messages'
ELASTIC_DOC_TYPE = 'msg'
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/42.0.2311.90 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_3) AppleWebKit/601.1.10 (KHTML, like Gecko) Version/8.0.5 Safari/601.1.10",
    "Mozilla/5.0 (Windows NT 6.3; WOW64; ; NCT50_AAP285C84A1328) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/42.0.2311.90 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.1 (KHTML, like Gecko) Chrome/22.0.1207.1 Safari/537.1",
    "Mozilla/5.0 (X11; CrOS i686 2268.111.0) AppleWebKit/536.11 (KHTML, like Gecko) Chrome/20.0.1132.57 Safari/536.11",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/536.6 (KHTML, like Gecko) Chrome/20.0.1092.0 Safari/536.6"
]

URLS = {
    "BASE": "https://www.facebook.com",
    "LOGIN": "https://m.facebook.com/login.php?login_attempt=1",
    "MOBILE": "https://m.facebook.com/",
    "THREADS": "https://www.facebook.com/ajax/mercury/threadlist_info.php",
    "MESSAGES": "https://www.facebook.com/ajax/mercury/thread_info.php"
}
# ############################
# ########## Utils ###########
# Log settings
log = logging.getLogger('log')
log.setLevel(logging.INFO)
# Creates the console handler
handler = logging.StreamHandler()
log.addHandler(handler)


def now():
    return int(time() * 1000)


def check_request(r, as_json=True):
    if not r.ok:
        raise Exception('Error when sending request: Got {} response'.format(r.status_code))

    content = r.content.decode('UTF-8')

    if content is None or len(content) == 0:
        raise Exception('Error when sending request: Got empty response')

    if as_json:
        content = content[content.index('{'):]
        try:
            j = json.loads(content)
        except ValueError:
            raise Exception('Error while parsing JSON: {}'.format(repr(content)))
        return j
    else:
        return content


def str_base(number, base):
    if number < 0:
        return '-' + str_base(-number, base)
    (d, m) = divmod(number, base)
    if d > 0:
        return str_base(d, base) + digitToChar(m)
    return digitToChar(m)


def digitToChar(digit):
    if digit < 10:
        return str(digit)
    return chr(ord('a') + digit - 10)
# ############################


class Thread(object):
    uid = str
    photo = str
    name = str
    last_message_timestamp = str
    message_count = int

    def __init__(self, uid, photo=None, name=None, last_message_timestamp=None, message_count=None):
        self.uid = str(uid)
        self.photo = photo
        self.name = name
        self.last_message_timestamp = last_message_timestamp
        self.message_count = message_count


class Group(Thread):
    participants = set
    nicknames = dict
    color = None
    emoji = str

    def __init__(self, uid, participants=None, nicknames=None, color=None, emoji=None, **kwargs):
        super(Group, self).__init__(uid, **kwargs)
        if participants is None:
            participants = set()
        self.participants = participants
        if nicknames is None:
            nicknames = []
        self.nicknames = nicknames
        self.color = color
        self.emoji = emoji


class Room(Group):
    admins = set
    approval_mode = bool
    approval_requests = set
    join_link = str
    privacy_mode = bool

    def __init__(self, uid, admins=None, approval_mode=None, approval_requests=None, join_link=None, privacy_mode=None, **kwargs):
        super(Room, self).__init__(uid, **kwargs)
        if admins is None:
            admins = set()
        self.admins = admins
        self.approval_mode = approval_mode
        if approval_requests is None:
            approval_requests = set()
        self.approval_requests = approval_requests
        self.join_link = join_link
        self.privacy_mode = privacy_mode


class Page(Thread):
    url = str
    city = str
    likes = int
    sub_title = str
    category = str

    def __init__(self, uid, url=None, city=None, likes=None, sub_title=None, category=None, **kwargs):
        super(Page, self).__init__(uid, **kwargs)
        self.url = url
        self.city = city
        self.likes = likes
        self.sub_title = sub_title
        self.category = category


class User(Thread):
    url = str  # profile url
    first_name = str
    last_name = str
    is_friend = bool
    gender = str
    affinity = float  # From 0 to 1. How close the client is to the user
    nickname = str
    own_nickname = str  # The clients nickname, as seen by the user
    color = None
    emoji = str

    def __init__(self, uid, url=None, first_name=None, last_name=None, is_friend=None, gender=None, affinity=None, nickname=None, own_nickname=None, color=None, emoji=None, **kwargs):
        super(User, self).__init__(uid, **kwargs)
        self.url = url
        self.first_name = first_name
        self.last_name = last_name
        self.is_friend = is_friend
        self.gender = gender
        self.affinity = affinity
        self.nickname = nickname
        self.own_nickname = own_nickname
        self.color = color
        self.emoji = emoji


class FBClient(object):
    listening = False
    uid = None

    def __init__(self, username, password, user_agent=None, max_tries=5, session_cookies=None):
        """
        :param username: Facebook username can be email/id/phone number
        :param password: Facebook account password
        :param user_agent: Custom user agent to use when sending requests
        :param max_tries: Maximum number of times to try logging in
        :param session_cookies: Cookies from a previous session (Will default to login if these are invalid)
        :type max_tries: int
        :type session_cookies: dict
        :raises: Exception on failed login
        """

        self.sticky, self.pool = (None, None)
        self._session = requests.session()
        self.req_counter = 1
        self.seq = "0"
        self.payloadDefault = {}
        self.client = 'mercury'
        self.default_thread_id = None
        self.default_thread_type = None

        if not user_agent:
            user_agent = choice(USER_AGENTS)

        self._header = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': URLS['BASE'],
            'Origin': URLS['BASE'],
            'User-Agent': user_agent,
            'Connection': 'keep-alive',
        }

        # If session cookies aren't set, not properly loaded or gives us an invalid session, then do the login
        if not session_cookies or not self.setSession(session_cookies) or not self.isLoggedIn():
            self.login(username, password, max_tries)
        else:
            self.email = username
            self.password = password

    def isLoggedIn(self):
        """
        Sends a request to Facebook to check the login status

        :return: True if the client is still logged in
        :rtype: bool
        """
        # Send a request to the login url, to see if we're directed to the home page
        r = self._session.get(URLS['LOGIN'], headers=self._header, params=None, timeout=30)
        return 'home' in r.url

    def setSession(self, session_cookies):
        """Loads session cookies

        :param session_cookies: A dictionay containing session cookies
        :type session_cookies: dict
        :return: False if `session_cookies` does not contain proper cookies
        :rtype: bool
        """

        # Quick check to see if session_cookies is formatted properly
        if not session_cookies or 'c_user' not in session_cookies:
            return False

        try:
            # Load cookies into current session
            self._session.cookies = requests.cookies.merge_cookies(self._session.cookies, session_cookies)
            self._postLogin()
        except Exception as e:
            print('Failed loading session', e)
            self._resetValues()
            return False
        return True

    def _postLogin(self):
        self.payloadDefault = {}
        self.client_id = hex(int(random() * 2147483648))[2:]
        self.start_time = now()
        self.uid = self._session.cookies.get_dict().get('c_user')
        if self.uid is None:
            raise Exception('Could not find c_user cookie')
        self.uid = str(self.uid)
        self.user_channel = "p_" + self.uid
        self.ttstamp = ''

        r = self._get(URLS['BASE'])
        soup = bs(r.text, "lxml")
        self.fb_dtsg = soup.find("input", {'name': 'fb_dtsg'})['value']
        self.fb_h = soup.find("input", {'name': 'h'})['value']
        for i in self.fb_dtsg:
            self.ttstamp += str(ord(i))
        self.ttstamp += '2'
        # Set default payload
        self.payloadDefault['__rev'] = int(r.text.split('"client_revision":', 1)[1].split(",", 1)[0])
        self.payloadDefault['__user'] = self.uid
        self.payloadDefault['__a'] = '1'
        self.payloadDefault['ttstamp'] = self.ttstamp
        self.payloadDefault['fb_dtsg'] = self.fb_dtsg

        self.form = {
            'channel': self.user_channel,
            'partition': '-2',
            'clientid': self.client_id,
            'viewer_uid': self.uid,
            'uid': self.uid,
            'state': 'active',
            'format': 'json',
            'idle': 0,
            'cap': '8'
        }

        self.prev = now()
        self.tmp_prev = now()
        self.last_sync = now()
        # extract from <script>, hack but needed, future *fixme*
        self.full_name = soup.find_all('script')[4].get_text().split('"NAME"')[1].split('"')[1]

    def _resetValues(self):
        self.payloadDefault = {}
        self._session = requests.session()
        self.req_counter = 1
        self.seq = "0"
        self.uid = None

    def _get(self, url, query=None, timeout=30, fix_request=False, as_json=False, error_retries=3):
        payload = self._generatePayload(query)
        r = self._session.get(url, headers=self._header, params=payload, timeout=timeout)
        if not fix_request:
            return r
        try:
            return check_request(r, as_json=as_json)
        except Exception as e:
            if error_retries > 0:
                return self._get(url, query=query, timeout=timeout, fix_request=fix_request, as_json=as_json,
                                 error_retries=error_retries - 1)
            raise e

    def _generatePayload(self, query):
        """Adds the following defaults to the payload: __rev, __user, __a, ttstamp, fb_dtsg, __req"""
        payload = self.payloadDefault.copy()
        if query:
            payload.update(query)
        payload['__req'] = str_base(self.req_counter, 36)
        payload['seq'] = self.seq
        self.req_counter += 1
        return payload

    def login(self, email, password, max_tries=5):
        """
        Uses `email` and `password` to login the user (If the user is already logged in, this will do a re-login)

        :param email: Facebook `email` or `id` or `phone number`
        :param password: Facebook account password
        :param max_tries: Maximum number of times to try logging in
        :type max_tries: int
        :raises: Exception on failed login
        """
        log.info("Logging in {}...".format(email))

        if max_tries < 1:
            raise Exception('Cannot login: max_tries should be at least one')

        if not (email and password):
            raise Exception('Email and password not set')

        self.email = email
        self.password = password

        for i in range(1, max_tries + 1):
            login_successful, login_url = self._login()
            if not login_successful:
                log.warning('Attempt #{} failed{}'.format(i, {True: ', retrying'}.get(i < max_tries, '')))
                sleep(1)
                continue
            else:
                log.info("Logging in {}...".format(email))
                break
        else:
            raise Exception('Login failed. Check email/password. (Failed on url: {})'.format(login_url))

    def _login(self):
        if not (self.email and self.password):
            raise Exception("Email and password not found.")

        soup = bs(self._get(URLS['MOBILE']).text, "lxml")
        data = dict((elem['name'], elem['value']) for elem in soup.findAll("input") if
                    elem.has_attr('value') and elem.has_attr('name'))
        data['email'] = self.email
        data['pass'] = self.password
        data['login'] = 'Log In'

        r = self._cleanPost(URLS['LOGIN'], data)

        if 'home' in r.url:
            self._postLogin()
            return True, r.url
        else:
            return False, r.url

    def _cleanGet(self, url, query=None, timeout=30):
        return self._session.get(url, headers=self._header, params=query, timeout=timeout)

    def _cleanPost(self, url, query=None, timeout=30):
        self.req_counter += 1
        return self._session.post(url, headers=self._header, data=query, timeout=timeout)

    def _post(self, url, query=None, timeout=30, fix_request=False, as_json=False, error_retries=3):
        payload = self._generatePayload(query)
        r = self._session.post(url, headers=self._header, data=payload, timeout=timeout)
        if not fix_request:
            return r
        try:
            return check_request(r, as_json=as_json)
        except Exception as e:
            if error_retries > 0 :
                return self._post(url, query=query, timeout=timeout, fix_request=fix_request, as_json=as_json,
                                  error_retries=error_retries - 1)
            raise e

    def _getThread(self, given_thread_id=None, given_thread_type=None):
        """
        Checks if thread ID is given, checks if default is set and returns correct values

        :raises ValueError: If thread ID is not given and there is no default
        :return: Thread ID and thread type
        :rtype: tuple
        """
        if given_thread_id is None:
            if self.default_thread_id is not None:
                return self.default_thread_id, self.default_thread_type
            else:
                raise ValueError('Thread ID is not set')
        else:
            return given_thread_id, given_thread_type

    def fetchThreadMessages(self, thread_id=None, limit=20, before=None):
        data = {
            'client': self.client,
            'messages[thread_fbids][' + thread_id + '][limit]': limit
            # 'messages[thread_fbids][' + thread_id + '][timestamp]': before
        }
        j = self._post(URLS['MESSAGES'], data, fix_request=True, as_json=True)

        resp = []
        msgs = j['payload']['actions']
        for m in msgs:
            resp.append({
                'timestamp': datetime.datetime.fromtimestamp(float(m['timestamp']) / 1000),
                'text': m['body'],
                'sender_id': m['author'].split(':')[1],
                'is_read': False if m['is_unread'] else True
            })
        return resp

    def fetchThreadList(self, offset=0, limit=20, thread_location='inbox'):
        """Get thread list of your facebook account

        :param offset: The offset, from where in the list to recieve threads from
        :param limit: Max. number of threads to retrieve. Capped at 20
        :param thread_location: 'inbox', 'pending', 'action:archived' or 'other'
        :type offset: int
        :type limit: int
        :return: :class:`models.Thread` objects
        :rtype: list
        :raises: FBchatException if request failed
        """
        data = {
            'client': self.client,
            thread_location + '[offset]': offset,
            thread_location + '[limit]': limit,
        }

        j = self._post(URLS['THREADS'], data, fix_request=True, as_json=True)
        if j.get('payload') is None:
            raise Exception('Missing payload: {}, with data: {}'.format(j, data))

        participants = {}
        if 'participants' in j['payload']:
            for p in j['payload']['participants']:
                if p['type'] == 'page':
                    participants[p['fbid']] = Page(p['fbid'], url=p['href'], photo=p['image_src'], name=p['name'])
                elif p['type'] == 'user':
                    participants[p['fbid']] = User(p['fbid'], url=p['href'], first_name=p['short_name'],
                                                   is_friend=p['is_friend'], gender=p['gender'],
                                                   photo=p['image_src'], name=p['name'])
                else:
                    raise Exception('A participant had an unknown type {}: {}'.format(p['type'], p))

        entries = []
        if 'threads' in j['payload']:
            for k in j['payload']['threads']:
                if k['thread_type'] == 1:
                    if k['other_user_fbid'] not in participants:
                        raise Exception('The thread {} was not in participants: {}'.format(k, j['payload']))
                    participants[k['other_user_fbid']].message_count = k['message_count']
                    entries.append(participants[k['other_user_fbid']])
                elif k['thread_type'] == 2:
                    entries.append(
                        Group(k['thread_fbid'], participants=set([p.strip('fbid:') for p in k['participants']]),
                              photo=k['image_src'], name=k['name'], message_count=k['message_count']))
                elif k['thread_type'] == 3:
                    entries.append(Room(
                        k['thread_fbid'],
                        participants=set(p.lstrip('fbid:') for p in k['participants']),
                        photo=k['image_src'],
                        name=k['name'],
                        message_count=k['message_count'],
                        admins=set(p.lstrip('fbid:') for p in k['admin_ids']),
                        approval_mode=k['approval_mode'],
                        approval_requests=set(p.lstrip('fbid:') for p in k['approval_queue_ids']),
                        join_link=k['joinable_mode']['link']
                    ))
                else:
                    raise Exception('A thread had an unknown thread type: {}'.format(k))

        return entries

if __name__ == "__main__":
    client = FBClient(args.username, args.password)
    es = Elasticsearch()

    threads = client.fetchThreadList()
    threads += client.fetchThreadList(offset=20, limit=10)

    for user in threads:
        messages = client.fetchThreadMessages(thread_id=user.uid, limit=10)
        for m in messages:
            sender_is_author = user.uid == m.get('sender_id')
            user_is_not_author = user.uid != m.get('sender_id')
            m['sender'] = user.name if sender_is_author else client.full_name
            m['receiver'] = user.name if user_is_not_author else client.full_name
            m['receiver_id'] = user.uid if user_is_not_author else client.uid
            # index document in elasticsearch
            res = es.index(index=ELASTIC_INDEX_ID, doc_type=ELASTIC_DOC_TYPE, body=m)
            m['is_created'] = res['created']
            print(m)

    es.indices.refresh(index=ELASTIC_INDEX_ID)

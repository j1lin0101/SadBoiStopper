# you will need to create an app at https://developer.spotify.com/my-applications/ in order to get a key and secret

def pretty(obj):
    return json.dumps(obj, sort_keys=True, indent=2)

from secrets import CLIENT_ID, CLIENT_SECRET

GRANT_TYPE = 'authorization_code'

import webapp2, urllib2, os, urllib, json, jinja2, logging, sys, time
import base64, Cookie, hashlib, hmac, email
from google.appengine.ext import db
from google.appengine.api import urlfetch

JINJA_ENVIRONMENT = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
                                       extensions=['jinja2.ext.autoescape'],
                                       autoescape=True)


### this is our user database model. We will use it to store the access_token
class User(db.Model):
    uid = db.StringProperty(required=True)
    displayname = db.StringProperty(required=False)
    img = db.StringProperty(required=False)
    access_token = db.StringProperty(required=True)
    refresh_token = db.StringProperty(required=False)
    profile_url = db.StringProperty(required=False)
    api_url = db.StringProperty(required=False)


### helper functions

### We have some cookie functions here. This lets us be careful
### to make sure that a  malicious user can't spoof your user ID in
### their cookie and then use our site to do things on your behalf
def set_cookie(response, name, value, domain=None, path="/", expires=None):
    """Generates and signs a cookie for the give name/value"""
    timestamp = str(int(time.time()))
    value = base64.b64encode(value)
    signature = cookie_signature(value, timestamp)
    cookie = Cookie.BaseCookie()
    cookie[name] = "|".join([value, timestamp, signature])
    cookie[name]["path"] = path
    if domain: cookie[name]["domain"] = domain
    if expires:
        cookie[name]["expires"] = email.utils.formatdate(
            expires, localtime=False, usegmt=True)
    response.headers.add("Set-Cookie", cookie.output()[12:])


def parse_cookie(value):
    if not value: return None
    parts = value.split("|")
    if len(parts) != 3: return None
    if cookie_signature(parts[0], parts[1]) != parts[2]:
        logging.warning("Invalid cookie signature %r", value)
        return None
    timestamp = int(parts[1])
    if timestamp < time.time() - 30 * 86400:
        logging.warning("Expired cookie %r", value)
        return None
    try:
        return base64.b64decode(parts[0]).strip()
    except:
        return None


def cookie_signature(*parts):
    chash = hmac.new(CLIENT_SECRET, digestmod=hashlib.sha1)
    for part in parts: chash.update(part)
    return chash.hexdigest()

### this adds a header with the user's access_token to Spotify requests
def spotifyurlfetch(url, access_token, params=None):
    headers = {'Authorization': 'Bearer ' + access_token}
    response = urlfetch.fetch(url, method=urlfetch.GET, payload=params, headers=headers)
    logging.info(url)
    return response.content

def spotifyurlpost(url, access_token, params=None):
    headers = {'Authorization': 'Bearer ' + access_token, "Content-Type": "application/json"}
    response = urlfetch.fetch(url, method=urlfetch.POST, payload=params, headers=headers)
    logging.info("Making a post " + url)
    return response.content

# INPUT: list of song valences
# OUTPUT: overall sadness of the list
#         Total Valence > 0.5 --> Happy
#         Total Valence = 0.5 --> Neutral
#         Total Valence < 0.5 --> Sad

def determineOverallMood(valences):
    songNum = len(valences)
    totalValence = sum(valences)

    meanValence = totalValence/songNum

    if meanValence > 0.5:
        return ("Happy", meanValence)
    elif meanValence == 0.5:
        return ("Neutral", meanValence)
    return ("Sad", meanValence)

def getTopSongsForArists(id, access_token):
    url = "https://api.spotify.com/v1/artists/%s/top-tracks?country=US"%id
    songsData = json.loads(spotifyurlfetch(url=url, access_token=access_token))["tracks"]

    songs = []
    for song in songsData:
        songId = song["id"]
        songurl = "https://api.spotify.com/v1/audio-features/%s" % songId
        songInfo = json.loads(spotifyurlfetch(songurl, access_token))
        songs.append(songInfo)
    return songs

class BaseHandler(webapp2.RequestHandler):
    # @property followed by def current_user makes so that if x is an instance
    # of BaseHandler, x.current_user can be referred to, which has the effect of
    # invoking x.current_user()
    @property
    def current_user(self):
        """Returns the logged in Spotify user, or None if unconnected."""
        if not hasattr(self, "_current_user"):
            self._current_user = None
            # find the user_id in a cookie
            user_id = parse_cookie(self.request.cookies.get("spotify_user"))
            if user_id:
                self._current_user = User.get_by_key_name(user_id)
        return self._current_user

    def new_playlist(self):
        if not hasattr(self, "_new_playlist"):
            self._new_playlist = None
            songs = parse_cookie(self.request.cookies.get("new_playlist"))
            if songs:
                self._new_playlist = songs
        return self._new_playlist

    def valence(self):
        if not hasattr(self, "_valence"):
            self._valence = None
            valence = parse_cookie(self.request.cookies.get("valence"))
            if valence:
                self._valence = valence
            return self._valence

### this will handle our home page
class HomeHandler(BaseHandler):
    def get(self):
        template = JINJA_ENVIRONMENT.get_template('oauth.html')

        # check if they are logged in
        user = self.current_user
        tvals = {'current_user': user}

        if user != None:

            url = "https://api.spotify.com/v1/me/player/recently-played"
            response = json.loads(spotifyurlfetch(url, user.access_token))
            if not response:
                self.redirect(self, "/auth/login")
            songs = response["items"]

            tvals["recents"] = songs
            valences = {}

            for song in songs:
                songId = song["track"]["id"]
                songurl = "https://api.spotify.com/v1/audio-features/%s" % songId
                songInfo = json.loads(spotifyurlfetch(songurl, user.access_token))
                songValence = songInfo["valence"]
                logging.info(pretty("Valence: %f" % songValence))
                valences[songId] = songValence

            tvals["valences"] = valences
            overallValence = determineOverallMood([valences[song] for song in valences])[0]

            tvals["overallValence"] = overallValence
            set_cookie(self.response, "valence", str(overallValence))

            tvals["meanValence"] = round(determineOverallMood([valences[song] for song in valences])[1], 3)
        self.response.write(template.render(tvals))

    ### this handler will handle our authorization requests

class PlaylistHandler(BaseHandler):
    def get(self):
        template = JINJA_ENVIRONMENT.get_template('playlist.html')

        user = self.current_user
        tvals = {'current_user': user, "valence": self.valence()}

        if user != None:
            ## if so, get their playlists
            url = "https://api.spotify.com/v1/users/%s/playlists" % user.uid
            ## in the future, should make this more robust so it checks if the access_token
            ## is still valid and retrieves a new one using refresh_token if not
            response = json.loads(spotifyurlfetch(url, user.access_token))

            tvals["playlists"] = response["items"]

        self.response.write(template.render(tvals))

class CreatePlaylistHandler(BaseHandler):
    def get(self):
        user = self.current_user

        if user != None:
            userPlaylisturl = "https://api.spotify.com/v1/users/%s/playlists" % user.uid
            response = spotifyurlpost(userPlaylisturl, user.access_token, params=json.dumps({"name": "Sadboi Stopper"}))
            playlistId = json.loads(response)["id"]
            set_cookie(self.response, "new_playlist", str(playlistId))
            logging.info(playlistId)

            recentsURL = "https://api.spotify.com/v1/me/player/recently-played"
            recents = json.loads(spotifyurlfetch(recentsURL, user.access_token))
            songs = recents["items"]
            artists = {}

            for song in songs:
                artist = song["track"]["album"]["artists"][0]["name"]
                id = song["track"]["album"]["artists"][0]["id"]
                if artist not in artists:
                    artists[artist] = id

            artistsTopSongs = []
            for artistId in artists:
                artistsTopSongs.append(getTopSongsForArists(artists[artistId], user.access_token))
            # logging.info(pretty(artistsTopSongs))

            happySongs = []
            for songsInfo in artistsTopSongs:
               for song in songsInfo:
                   songId = song["id"]
                   songurl = "https://api.spotify.com/v1/audio-features/%s" % songId
                   songInfo = json.loads(spotifyurlfetch(songurl, user.access_token))
                   songValence = songInfo["valence"]
                   if songValence > 0.5:
                       happySongs.append(songId)
            happySongs = ["spotify:track:" + song for song in happySongs]
            playlistUrl = "https://api.spotify.com/v1/playlists/%s/tracks"%playlistId + "?uris=" + ",".join(happySongs)

            tracksToPlay = spotifyurlpost(playlistUrl, user.access_token)

            self.redirect("/playlist/new")

class NewPlaylistHandler(BaseHandler):
    def get(self):
        tvals = {}
        tvals["current_user"] = self.current_user
        template = JINJA_ENVIRONMENT.get_template('newplaylist.html')

        playlistId = self.new_playlist()
        user = self.current_user

        playlistUrl = "https://api.spotify.com/v1/playlists/%s"%playlistId
        playlistResponse = json.loads(spotifyurlfetch(playlistUrl, user.access_token))
        playlistName = playlistResponse["name"]
        tvals["playlist_name"] = playlistName

        playlistTracksUrl = "https://api.spotify.com/v1/playlists/%s/tracks"%playlistId + "?fields=items(track(artists,name,href,album(name,href)))"
        response = json.loads(spotifyurlfetch(playlistTracksUrl, user.access_token))["items"]

        songs = {}

        for song in response:
            artists = []
            artistsInfo = song["track"]["artists"]
            for artist in artistsInfo:
                artists.append(artist["name"])

            name = song["track"]["name"]
            songs[name] = {}
            songs[name]["artist"] = ", ".join(artists)

        tvals["song_list"] = songs
        self.response.write(template.render(tvals))


class LoginHandler(BaseHandler):
    def get(self):
        # after  login; redirected here
        # did we get a successful login back?
        args = {}
        args['client_id'] = CLIENT_ID

        verification_code = self.request.get("code")
        if verification_code:
            # if so, we will use code to get the access_token from Spotify
            # This corresponds to STEP 4 in https://developer.spotify.com/web-api/authorization-guide/

            args["client_secret"] = CLIENT_SECRET
            args["grant_type"] = GRANT_TYPE
            args["code"] = verification_code  # the code we got back from Spotify
            args['redirect_uri'] = self.request.path_url  # the current page

            # We need to make a post request, according to the documentation

            # headers = {'content-type': 'application/x-www-form-urlencoded'}
            url = "https://accounts.spotify.com/api/token"
            response = urlfetch.fetch(url, method=urlfetch.POST, payload=urllib.urlencode(args))
            response_dict = json.loads(response.content)
            logging.info(response_dict["access_token"])
            access_token = response_dict["access_token"]
            refresh_token = response_dict["refresh_token"]

            # Download the user profile. Save profile and access_token
            # in Datastore; we'll need the access_token later

            ## the user profile is at https://api.spotify.com/v1/me
            profile = json.loads(spotifyurlfetch('https://api.spotify.com/v1/me', access_token))
            logging.info(profile)

            user = User(key_name=str(profile["id"]), uid=str(profile["id"]),
                        displayname=str(profile["display_name"]), access_token=access_token,
                        profile_url=profile["external_urls"]["spotify"], api_url=profile["href"],
                        refresh_token=refresh_token)
            if profile.get('images') is not None:
                user.img = profile["images"][0]["url"]
            user.put()

            ## set a cookie so we can find the user later
            set_cookie(self.response, "spotify_user", str(user.uid))

            ## okay, all done, send them back to the App's home page
            self.redirect("/")
        else:
            # not logged in yet-- send the user to Spotify to do that
            # This corresponds to STEP 1 in https://developer.spotify.com/web-api/authorization-guide/

            args['redirect_uri'] = self.request.path_url
            args['response_type'] = "code"
            # ask for the necessary permissions - see details at https://developer.spotify.com/web-api/using-scopes/
            args['scope'] = "user-read-recently-played user-library-modify playlist-modify-private playlist-modify-public playlist-read-collaborative"
            url = "https://accounts.spotify.com/authorize?" + urllib.urlencode(args)
            logging.info(url)
            self.redirect(url)


class LogoutHandler(BaseHandler):
    def get(self):
        set_cookie(self.response, "spotify_user", "")
        self.redirect("/")


application = webapp2.WSGIApplication([ \
    ("/", HomeHandler),
    ("/playlist", PlaylistHandler),
    ("/playlist/create", CreatePlaylistHandler),
    ("/auth/login", LoginHandler),
    ("/playlist/new", NewPlaylistHandler),
    ("/auth/logout", LogoutHandler)
], debug=True)
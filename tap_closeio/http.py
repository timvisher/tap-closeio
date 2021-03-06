import time
from collections import namedtuple
import requests
from requests.auth import HTTPBasicAuth
from singer import metrics

BASE_URL = "https://app.close.io/api/v1"
PER_PAGE = 100


class RateLimitException(Exception):
    pass


def _join(a, b):
    return a.rstrip("/") + "/" + b.lstrip("/")


def url(path):
    return _join(BASE_URL, path)


def create_get_request(path, **kwargs):
    return requests.Request(method="GET", url=url(path), **kwargs)


class Client(object):
    def __init__(self, config):
        self.user_agent = config.get("user_agent")
        self.session = requests.Session()
        self.auth = HTTPBasicAuth(config["api_key"], "")

    def prepare_and_send(self, request):
        if self.user_agent:
            request.headers["User-Agent"] = self.user_agent
        request.auth = self.auth
        return self.session.send(request.prepare())

    def request_with_handling(self, tap_stream_id, request):
        with metrics.http_request_timer(tap_stream_id) as timer:
            resp = self.prepare_and_send(request)
            timer.tags[metrics.Tag.http_status_code] = resp.status_code
        json = resp.json()
        # if we're hitting the rate limit cap, sleep until the limit resets
        if resp.headers.get('X-Rate-Limit-Remaining') == "0":
            time.sleep(int(resp.headers['X-Rate-Limit-Reset']))
        # if we're already over the limit, we'll get a 429
        # sleep for the rate_reset seconds and then retry
        if resp.status_code == 429:
            time.sleep(json["rate_reset"])
            return self.request_with_handling(tap_stream_id, request)
        resp.raise_for_status()
        return json

Page = namedtuple("Page", ("records", "skip", "next_skip"))


def paginate(client, tap_stream_id, request, *, skip=0):
    request.params = request.params or {}
    request.params["_limit"] = PER_PAGE
    while True:
        request.params["_skip"] = skip
        response = client.request_with_handling(tap_stream_id, request)
        next_skip = skip + len(response["data"])
        yield Page(response["data"], skip, next_skip)
        if not response.get("has_more"):
            break
        skip = next_skip

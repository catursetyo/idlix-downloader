"""
Helper Class for IDLIX Downloader & IDLIX Player CLI

Update  :   28-11-2025
Author  :   sandroputraa
"""

import os
import re
import json
import time
import m3u8
import shutil
import zipfile
import requests
import subprocess
import m3u8_To_MP4
from loguru import logger
from bs4 import BeautifulSoup
from urllib.parse import unquote, urljoin, urlparse
from vtt_to_srt.vtt_to_srt import ConvertFile
from curl_cffi import requests as cffi_requests
from src.CryptoJsAesHelper import CryptoJsAes, dec


class IdlixHelper:
    BASE_WEB_URL = "https://z2.idlixku.com/"
    TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
    BASE_STATIC_HEADERS = {
        "Connection": "keep-alive",
        "sec-ch-ua": "Not)A;Brand;v=99, Google Chrome;v=127, Chromium;v=127",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "Windows",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Referer": BASE_WEB_URL,
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8"
    }

    def __init__(self):
        self.base_web_url = self.BASE_WEB_URL
        self.api_base_url = urljoin(self.base_web_url, "api")
        self.provider = None
        self.content_type = "movie"
        self.poster = None
        self.m3u8_url = None
        self.video_id = None
        self.embed_url = None
        self.video_name = None
        self.is_subtitle = None
        self.variant_playlist = None
        self.next_subtitles = []
        self.max_height = None
        self.request = cffi_requests.Session(
            impersonate="chrome124",
            headers=self.BASE_STATIC_HEADERS,
            debug=False,
        )

        # Proxy Example
        # self.request.proxies = {
        #    'https': ''
        # }

        # FFMPEG
        if os.name == 'nt':
            for _ in os.environ.get('path').split(';'):
                if 'ffmpeg' in _:
                    logger.info(f'FFMPEG Found: {_}')
                    break
            else:
                if not os.path.exists('ffmpeg-release-essentials.zip'):
                    self.download_ffmpeg()
                logger.warning('FFMPEG not set in PATH, Trying set PATH')
                try:
                    with zipfile.ZipFile('ffmpeg-release-essentials.zip', 'r') as zip_ref:
                        zip_ref.extractall(
                            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg')
                        )
                    logger.success('Success Extracting ffmpeg')
                    path = ""
                    for _ in os.listdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg')):
                        if 'ffmpeg' in _:
                            logger.info(f'Found: {os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg", _, "bin")}')
                            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg", _, "bin")
                            break
                    else:
                        logger.error('FFMPEG not found, please install ffmpeg first before running this script')
                    subprocess.call(["setx", "PATH", "%PATH%;" + path])
                    logger.success('FFMPEG PATH set successfully, Please restart the program')
                    exit()
                except Exception as e:
                    print(f'Error: {e}')
        else:
            if not shutil.which('ffmpeg'):
                logger.error('FFMPEG not found, please install ffmpeg first before running this script')
                exit()

    @staticmethod
    def download_ffmpeg():
        try:
            logger.info('Downloading ffmpeg')
            content = requests.get(
                url='https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
                stream=True
            )
            with open("ffmpeg-release-essentials.zip", mode="wb") as file:
                for chunk in content.iter_content(chunk_size=1024):
                    print(
                        '\rDownloading: {} MB of {} MB'.format(
                            round(os.path.getsize('ffmpeg-release-essentials.zip') / 1024 / 1024, 2),
                            round(int(content.headers.get('Content-Length', 0)) / 1024 / 1024, 2)
                        ),
                        end=''
                    )
                    file.write(chunk)
            print()
            logger.success('Downloaded ffmpeg')
        except Exception as e:
            print(f'Error: {e}')

    @staticmethod
    def _origin_from_url(url):
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}/"

    @staticmethod
    def _year_from_date(value):
        if isinstance(value, str) and len(value) >= 4:
            return value[:4]
        return ""

    @staticmethod
    def _format_bandwidth(bandwidth):
        if not bandwidth:
            return "unknown bandwidth"
        if bandwidth >= 1_000_000:
            return f"{bandwidth / 1_000_000:.1f} Mbps"
        return f"{round(bandwidth / 1000)} Kbps"

    @staticmethod
    def _parse_next_route(path):
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "movie":
            return "movie", parts[1]
        if len(parts) >= 2 and parts[0] in ("series", "tv-series"):
            return "tv_series", parts[1]
        return None, None

    def _set_base_url(self, url):
        origin = self._origin_from_url(url)
        if origin:
            self.base_web_url = origin
            self.api_base_url = urljoin(origin, "api")

    def _headers(self, referer=None, accept=None, content_type=None):
        headers = dict(self.BASE_STATIC_HEADERS)
        headers["Referer"] = referer or self.base_web_url
        if accept:
            headers["Accept"] = accept
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _json_headers(self, referer=None, content_type=None):
        headers = self._headers(
            referer=referer,
            accept="application/json, text/plain, */*",
            content_type=content_type,
        )
        headers["Origin"] = self.base_web_url.rstrip("/")
        return headers

    def _api_url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.api_base_url.rstrip("/") + "/", path.lstrip("/"))

    def _api_get(self, path, referer=None):
        request = self.request.get(
            url=self._api_url(path),
            headers=self._json_headers(referer=referer),
            timeout=30,
        )
        if request.status_code < 200 or request.status_code >= 300:
            raise RuntimeError(f"API GET {path} failed: HTTP {request.status_code} - {request.text[:200]}")
        return request.json()

    def _api_post(self, path, payload, referer=None, content_type="application/json"):
        request = self.request.post(
            url=self._api_url(path),
            headers=self._json_headers(referer=referer, content_type=content_type),
            data=json.dumps(payload),
            timeout=30,
        )
        if request.status_code < 200 or request.status_code >= 300:
            raise RuntimeError(f"API POST {path} failed: HTTP {request.status_code} - {request.text[:200]}")
        return request.json()

    def _tmdb_image_url(self, path):
        if not path:
            return None
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.TMDB_IMAGE_BASE}{path}"

    def _build_video_name(self, data):
        title = data.get("title") or data.get("name") or data.get("slug") or "IDLIX Video"
        year = self._year_from_date(data.get("releaseDate") or data.get("firstAirDate"))
        return f"{title} ({year})" if year else title

    def _variant_label(self, playlist):
        resolution = playlist.stream_info.resolution
        if resolution and len(resolution) == 2:
            return f"{resolution[0]}x{resolution[1]}"
        return self._format_bandwidth(playlist.stream_info.bandwidth)

    def _playlist_variants(self, playlist):
        variants = []
        for index, variant in enumerate(playlist.playlists):
            variants.append({
                "bandwidth": variant.stream_info.bandwidth,
                "resolution": self._variant_label(variant),
                "uri": variant.absolute_uri or urljoin(self.m3u8_url, variant.uri),
                "id": str(index),
            })
        return variants

    def _resolve_next_movie(self, url, slug):
        data = self._api_get(f"/movies/{slug}", referer=url)
        self.provider = "next"
        self.content_type = "movie"
        self.video_id = data.get("id")
        self.video_name = self._build_video_name(data)
        self.poster = self._tmdb_image_url(data.get("posterPath") or data.get("backdropPath"))
        if not self.video_id:
            raise RuntimeError("Movie id not found in API response")
        return {
            "status": True,
            "video_id": self.video_id,
            "video_name": self.video_name,
            "poster": self.poster,
        }

    def _track_view(self):
        try:
            self._api_post(
                "/views/track",
                {"contentType": self.content_type, "contentId": self.video_id},
                referer=self.embed_url or self.base_web_url,
            )
        except Exception:
            pass

    def _claim_next_gate(self, play_info):
        gate = play_info
        for _ in range(12):
            server_now = gate.get("serverNow") or int(time.time() * 1000)
            unlock_at = gate.get("unlockAt") or server_now
            wait_seconds = max(0, (unlock_at - server_now) / 1000)
            if wait_seconds:
                logger.info(f"Waiting gate countdown: {round(wait_seconds, 1)}s")
                time.sleep(wait_seconds + 0.5)

            claimed = self._api_post(
                "/watch/session/claim",
                {"gateToken": gate.get("gateToken")},
                referer=self.embed_url or self.base_web_url,
            )
            if claimed.get("kind") == "pentos":
                return claimed
            if claimed.get("kind") == "gate":
                gate = claimed
                continue

            remaining_ms = claimed.get("remainingMs")
            if remaining_ms:
                time.sleep(min(max(remaining_ms / 1000, 0.25), 5))
                continue

            raise RuntimeError(f"Unexpected gate response: {claimed}")

        raise RuntimeError("Playback session is not ready after gate retries")

    def _redeem_pentos(self, play_info):
        redeem_url = play_info.get("redeemUrl")
        claim = play_info.get("claim")
        if not redeem_url or not claim:
            raise RuntimeError("Missing pentos redeemUrl or claim")

        request = self.request.post(
            url=redeem_url,
            headers={
                "Content-Type": "text/plain",
                "Accept": "application/json, text/plain, */*",
                "User-Agent": self.BASE_STATIC_HEADERS["User-Agent"],
            },
            data=json.dumps({"claim": claim}),
            timeout=30,
        )
        if request.status_code < 200 or request.status_code >= 300:
            raise RuntimeError(f"Pentos redeem failed: HTTP {request.status_code} - {request.text[:200]}")
        data = request.json()
        if not data.get("url"):
            raise RuntimeError("Pentos redeem response does not contain a playlist URL")
        return data

    def _get_next_m3u8_url(self):
        try:
            self._track_view()
            play_info = self._api_get(
                f"/watch/play-info/{self.content_type}/{self.video_id}",
                referer=self.embed_url or self.base_web_url,
            )
            if play_info.get("kind") == "gate":
                play_info = self._claim_next_gate(play_info)

            if play_info.get("kind") != "pentos":
                return {
                    "status": False,
                    "message": f"Unsupported play-info kind: {play_info.get('kind')}"
                }

            redeemed = self._redeem_pentos(play_info)
            self.m3u8_url = redeemed["url"]
            self.next_subtitles = redeemed.get("subtitles") or []
            self.max_height = play_info.get("maxHeight")
            self.variant_playlist = m3u8.load(self.m3u8_url)
            tmp_variant_playlist = self._playlist_variants(self.variant_playlist)

            return {
                "status": True,
                "m3u8_url": self.m3u8_url,
                "variant_playlist": tmp_variant_playlist,
                "is_variant_playlist": len(tmp_variant_playlist) > 1,
            }
        except Exception as error_get_next_m3u8_url:
            return {
                "status": False,
                "message": str(error_get_next_m3u8_url),
            }

    def get_home(self):
        try:
            self._set_base_url(self.BASE_WEB_URL)
            try:
                data = self._api_get("/movies?limit=20", referer=self.base_web_url)
                movies = data.get("data", data if isinstance(data, list) else [])
                tmp_featured = []
                for movie in movies:
                    if movie.get("contentType") not in (None, "movie"):
                        continue
                    slug = movie.get("slug")
                    if not slug:
                        continue
                    year = self._year_from_date(movie.get("releaseDate"))
                    tmp_featured.append({
                        "url": urljoin(self.base_web_url, f"/movie/{slug}"),
                        "title": movie.get("title") or slug,
                        "year": year,
                        "type": "movie",
                        "poster": self._tmdb_image_url(movie.get("posterPath")),
                    })
                if tmp_featured:
                    return {
                        'status': True,
                        'featured_movie': tmp_featured
                    }
            except Exception:
                pass

            request = self.request.get(
                url=self.base_web_url,
                headers=self._headers(referer=self.base_web_url),
                timeout=10
            )
            if request.status_code == 200:
                bs = BeautifulSoup(request.text, 'html.parser')
                tmp_featured = []
                featured_container = bs.find('div', {'class': 'items featured'})
                if not featured_container:
                    return {
                        'status': False,
                        'message': 'Featured movie list not found'
                    }
                for featured in featured_container.find_all('article'):

                    if featured.find('a').get('href').split('/')[3] == 'tvseries':
                        continue

                    tmp_featured.append({
                        "url": featured.find('a').get('href'),
                        "title": featured.find('h3').text,
                        "year": featured.find('span').text,
                        "type": featured.find('a').get('href').split('/')[3],
                        "poster": featured.find('img').get('src'),
                    })
                return {
                    'status': True,
                    'featured_movie': tmp_featured
                }
            else:
                return {
                    'status': False,
                    'message': 'Failed to get home page'
                }
        except Exception as error_get_home:
            return {
                'status': False,
                'message': str(error_get_home)
            }

    def get_video_data(self, url):
        if not url:
            return {
                'status': False,
                'message': 'URL is required'
            }
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return {
                'status': False,
                'message': 'Invalid URL'
            }

        self._set_base_url(url)
        request = self.request.get(
            url=url,
            headers=self._headers(referer=self.base_web_url),
            timeout=30,
        )
        if request.status_code != 200:
            return {
                'status': False,
                'message': f'Failed to get video data: HTTP {request.status_code}'
            }

        bs = BeautifulSoup(request.text, 'html.parser')
        dooplay_counter = bs.find('meta', {'id': 'dooplay-ajax-counter'})
        if dooplay_counter:
            request = self.request.get(
                url=url,
                headers=self._headers(referer=self.base_web_url),
            )
            if request.status_code == 200:
                bs = BeautifulSoup(request.text, 'html.parser')
                self.provider = "dooplay"
                self.content_type = "movie"
                self.video_id = bs.find('meta', {'id': 'dooplay-ajax-counter'}).get('data-postid')
                name_meta = bs.find('meta', {'itemprop': 'name'})
                image_meta = bs.find('img', {'itemprop': 'image'})
                self.video_name = unquote(name_meta.get('content')) if name_meta else "IDLIX Video"
                self.poster = image_meta.get('src') if image_meta else None
                return {
                    'status': True,
                    'video_id': self.video_id,
                    'video_name': self.video_name,
                    'poster': self.poster
                }
            else:
                return {
                    'status': False,
                    'message': 'Failed to get video data'
                }

        content_type, slug = self._parse_next_route(parsed.path)
        if content_type == "movie" and slug:
            try:
                return self._resolve_next_movie(url, slug)
            except Exception as error_resolve_next_movie:
                return {
                    'status': False,
                    'message': str(error_resolve_next_movie)
                }

        return {
            'status': False,
            'message': 'Unsupported URL. Only movie URLs are supported for the new IDLIX structure'
        }

    def get_embed_url(self):
        if not self.video_id:
            return {
                'status': False,
                'message': 'Video ID is required'
            }

        if self.provider == "next":
            self.embed_url = urljoin(
                self.base_web_url,
                f"api/watch/play-info/{self.content_type}/{self.video_id}"
            )
            return {
                'status': True,
                'embed_url': self.embed_url
            }

        try:
            request = self.request.post(
                url=urljoin(self.base_web_url, "wp-admin/admin-ajax.php"),
                headers=self._headers(
                    referer=self.base_web_url,
                    content_type="application/x-www-form-urlencoded; charset=UTF-8"
                ),
                data={
                    "action": "doo_player_ajax",
                    "post": self.video_id,
                    "nume": "1",
                    "type": "movie",
                }
            )
            if request.status_code == 200 and request.json().get('embed_url'):
                self.embed_url = CryptoJsAes.decrypt(
                    request.json().get('embed_url'),
                    dec(
                        request.json().get('key'),
                        json.loads(request.json().get('embed_url')).get('m')
                    )
                )
                return {
                    'status': True,
                    'embed_url': self.embed_url
                }
            else:
                return {
                    'status': False,
                    'message': 'Failed to get embed URL'
                }
        except Exception as error_get_embed_url:
            return {
                'status': False,
                'message': str(error_get_embed_url)
            }

    def get_m3u8_url(self):
        if not self.embed_url:
            return {
                'status': False,
                'message': 'Embed URL is required'
            }

        if self.provider == "next":
            return self._get_next_m3u8_url()

        if '/video/' in urlparse(self.embed_url).path:
            self.embed_url = urlparse(self.embed_url).path.split('/')[2]
        elif "=" in urlparse(self.embed_url).query:
            self.embed_url = urlparse(self.embed_url).query.split('=')[1]

        try:
            request = cffi_requests.post(
                url='https://jeniusplay.com/player/index.php',
                params={
                    "data": self.embed_url,
                    "do": "getVideo"
                },
                headers={
                    "Host": "jeniusplay.com",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                data={
                    "hash": self.embed_url,
                    "r": self.base_web_url,
                },
                impersonate="chrome",
            )

            if request.status_code == 200 and request.json().get('videoSource'):
                self.m3u8_url = request.json().get('videoSource').rsplit(".", 1)[0] + ".m3u8"
                self.variant_playlist = m3u8.load(self.m3u8_url)
                tmp_variant_playlist = self._playlist_variants(self.variant_playlist)
                is_variant_playlist = True if len(tmp_variant_playlist) > 1 else False
                return {
                    'status': True,
                    'm3u8_url': self.m3u8_url,
                    'variant_playlist': tmp_variant_playlist,
                    'is_variant_playlist': is_variant_playlist
                }
            else:
                return {
                    'status': False,
                    'message': 'Failed to get m3u8 URL'
                }
        except Exception as error_get_m3u8_url:
            return {
                'status': False,
                'message': str(error_get_m3u8_url)
            }

    def download_m3u8(self):
        try:
            if not self.m3u8_url:
                return {
                    'status': False,
                    'message': 'M3U8 URL is required'
                }
            if not os.path.exists(os.getcwd() + '/tmp/'):
                os.mkdir(os.getcwd() + '/tmp/')

            m3u8_To_MP4.multithread_download(
                m3u8_uri=self.m3u8_url,
                max_num_workers=10,
                mp4_file_name=self.video_name,
                mp4_file_dir=os.getcwd() + '/',
                tmpdir=os.getcwd() + '/tmp/'
            )
            shutil.rmtree(os.getcwd() + '/tmp/', ignore_errors=True)
            return {
                'status': True,
                'message': 'Download success',
                'path': os.getcwd() + '/' + self.video_name + '.mp4'
            }
        except Exception as error_download_m3u8:
            return {
                'status': False,
                'message': str(error_download_m3u8)
            }

    def get_subtitle(self, download=True):
        try:
            if self.provider == "next":
                if not self.next_subtitles:
                    self.is_subtitle = False
                    return {
                        'status': False,
                        'message': 'Subtitle not found'
                    }

                subtitle = next(
                    (
                        item for item in self.next_subtitles
                        if item.get("lang") == "id" or "indonesian" in item.get("label", "").lower()
                    ),
                    self.next_subtitles[0],
                )
                subtitle_url = subtitle.get("path")
                if not subtitle_url:
                    self.is_subtitle = False
                    return {
                        'status': False,
                        'message': 'Subtitle URL not found'
                    }

                if not download:
                    self.is_subtitle = True
                    return {
                        'status': True,
                        'subtitle': subtitle_url
                    }

                subtitle_request = self.request.get(
                    url=subtitle_url,
                    headers=self._headers(
                        referer=self.m3u8_url or self.base_web_url,
                        accept="text/vtt,*/*"
                    ),
                    timeout=30,
                )
                if subtitle_request.status_code != 200:
                    self.is_subtitle = False
                    return {
                        'status': False,
                        'message': f'Failed to download subtitle: HTTP {subtitle_request.status_code}'
                    }

                subtitle_base = self.video_name.replace(" ", "_")
                with open(subtitle_base + '.vtt', 'wb') as subtitle_file:
                    subtitle_file.write(subtitle_request.content)
                self.convert_vtt_to_srt(subtitle_base + '.vtt')
                self.is_subtitle = True
                return {
                    'status': True,
                    'subtitle': subtitle_base + '.srt',
                }

            if not self.embed_url:
                return {
                    'status': False,
                    'message': 'Embed URL is required'
                }

            request = cffi_requests.post(
                url='https://jeniusplay.com/player/index.php',
                params={
                    "data": self.embed_url,
                    "do": "getVideo"
                },
                headers={
                    "Host": "jeniusplay.com",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                data={
                    "hash": self.embed_url,
                    "r": self.base_web_url
                },
                impersonate="chrome",

            )
            regex_subtitle = re.search(r"var playerjsSubtitle = \"(.*)\";", request.text)
            if regex_subtitle:
                if download:
                    subtitle_request = requests.get(
                        url="https://" + regex_subtitle.group(1).split("https://")[1],
                    )
                    with open(self.video_name.replace(" ", "_") + '.vtt', 'wb') as subtitle_file:
                        subtitle_file.write(subtitle_request.content)
                    self.convert_vtt_to_srt(self.video_name.replace(" ", "_") + '.vtt')
                    self.is_subtitle = True
                    return {
                        'status': True,
                        'subtitle': self.video_name.replace(" ", "_") + '.srt',
                    }

                self.is_subtitle = True
                return {
                    'status': True,
                    'subtitle': "https://" + regex_subtitle.group(1).split("https://")[1]
                }
            else:
                self.is_subtitle = False
                return {
                    'status': False,
                    'message': 'Subtitle not found'
                }
        except Exception as error_get_subtitle:
            return {
                'status': False,
                'message': str(error_get_subtitle)
            }

    def play_m3u8(self):
        try:
            if not self.m3u8_url:
                return {
                    'status': False,
                    'message': 'M3U8 URL is required'
                }

            if self.is_subtitle:
                subprocess.call([
                    "ffplay",
                    "-i",
                    self.m3u8_url,
                    "-window_title",
                    self.video_name,
                    "-vf",
                    "subtitles=" + self.video_name.replace(" ", "_") + ".srt",
                    "-hide_banner",
                    "-loglevel",
                    "panic"
                ])

            subprocess.call([
                "ffplay",
                "-i",
                self.m3u8_url,
                "-window_title",
                self.video_name,
                "-hide_banner",
                "-loglevel",
                "panic"
            ])

            if self.is_subtitle and os.path.exists(self.video_name.replace(" ", "_") + '.srt'):
                os.remove(self.video_name.replace(" ", "_") + '.srt')
                os.remove(self.video_name.replace(" ", "_") + '.vtt')

            return {
                'status': True,
                'message': 'Playing m3u8'
            }
        except Exception as error_play_m3u8:
            return {
                'status': False,
                'message': str(error_play_m3u8)
            }

    @staticmethod
    def convert_vtt_to_srt(vtt_file):
        convert_file = ConvertFile(vtt_file, "utf-8")
        convert_file.convert()

    def set_m3u8_url(self, m3u8_url):
        if urlparse(m3u8_url).scheme:
            self.m3u8_url = m3u8_url
        elif self.m3u8_url:
            self.m3u8_url = urljoin(self.m3u8_url, m3u8_url)
        else:
            self.m3u8_url = urljoin("https://jeniusplay.com", m3u8_url)

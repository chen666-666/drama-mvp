"""DouK metadata resolver plus a restricted, streaming public-media transport."""
from urllib.parse import urljoin, urlparse
import httpx

from sources.douk import BRIDGE, SourceUnavailable, _cookie

CDN_DOMAINS = ("douyinvod.com", "douyin.com", "douyinpic.com", "bytecdn.cn", "byteimg.com", "douyinstatic.com", "amemv.com", "pstatp.com", "snssdk.com")


def bridge_request(endpoint, body):
    try:
        with httpx.Client(trust_env=False, timeout=50) as client:
            response = client.post(BRIDGE + endpoint, json={**body, "cookie": _cookie()})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise SourceUnavailable("failed", "本机采集服务未就绪或响应超时，请运行 start-search.ps1 并检查登录状态")
    status = data.get("status")
    if status != "ok":
        messages = {"needs_login": "抖音要求登录，请更新本机登录状态后恢复", "blocked": "抖音要求验证，请在官方页面完成后恢复",
                    "unavailable": "未取得可解析的原作者目录，请手动补充正片链接或稍后重试" if endpoint == "/directory" else "此视频当前无法公开访问，或详情接口未返回可下载的视频"}
        raise SourceUnavailable(status or "failed", messages.get(status, "没有取得可解析的数据，请检查工具及来源"))
    return data


def allowed_media_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or not any(host == domain or host.endswith("." + domain) for domain in CDN_DOMAINS):
        raise ValueError("来源未返回支持的公开视频地址，暂停下载以便核对")
    return url


class DouKDownloader:
    def resolve(self, video_id):
        return bridge_request("/media", {"video_id": video_id})

    def transfer(self, url, path, progress):
        # Re-resolve on retry. Signed CDN URLs and cookies never enter the database or logs.
        headers = {"Referer": "https://www.douyin.com/", "User-Agent": "Mozilla/5.0"}
        with httpx.Client(trust_env=False, timeout=httpx.Timeout(30, read=60), follow_redirects=False) as client:
            for _ in range(6):
                allowed_media_url(url)
                with client.stream("GET", url, headers=headers) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code in (401, 403):
                        raise SourceUnavailable("blocked", "视频地址暂时无法访问，请核对登录与公开状态后恢复")
                    response.raise_for_status()
                    size = 0
                    progress(0)
                    with path.open("wb") as target:
                        for chunk in response.iter_bytes(512 * 1024):
                            target.write(chunk)
                            size += len(chunk)
                            progress(size)
                    expected = response.headers.get("content-length")
                    if expected and int(expected) != size:
                        raise ValueError("视频传输未完成，保留失败原因以便重试")
                    return
            raise ValueError("视频地址重定向过多，请检查来源")

import re
import socket
import struct
import ctypes
from http.client import HTTPResponse
from typing import List, Dict, Union, Tuple, Any
from urllib.parse import urlparse

from nassl.ssl_client import OpenSslVersionEnum
from requests.models import Response
from sslyze import server_connectivity_tester
from sslyze.utils import ssl_connection_configurator, http_response_parser
from sslyze.utils.ssl_connection import SslConnection

from yawast.reporting.enums import Vulnerabilities as v
from yawast.scanner.plugins.evidence import Evidence
from yawast.scanner.plugins.http import response_scanner
from yawast.scanner.plugins.http.servers import apache_httpd, php, iis, nginx, python
from yawast.scanner.plugins.result import Result
from yawast.scanner.session import Session
from yawast.shared import network, utils, output

_checked_cookies: Dict[v, List[str]] = {}


def reset():
    global _checked_cookies

    _checked_cookies = {}


def get_header_issues(res: Response, raw: str, url: str) -> List[Result]:
    results: List[Result] = []

    try:
        headers = res.headers

        if "X-Powered-By" in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f'X-Powered-By Header Present: {headers["X-Powered-By"]} ({url})',
                    v.HTTP_HEADER_X_POWERED_BY,
                )
            )

            # check to see if this is a php version
            results += php.check_version(headers["X-Powered-By"], raw, url)

        if "X-XSS-Protection" in headers:
            # header is present, check the value
            if "0" in headers["X-XSS-Protection"]:
                results.append(
                    Result.from_evidence(
                        Evidence.from_response(res),
                        f"X-XSS-Protection Disabled Header Present ({url})",
                        v.HTTP_HEADER_X_XSS_PROTECTION_DISABLED,
                    )
                )
        else:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f"X-XSS-Protection Header Not Present ({url})",
                    Vulnerabilities.HTTP_HEADER_X_XSS_PROTECTION_MISSING,
                )
            )

        if "X-Runtime" in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f"X-Runtime Header Present; likely indicates a RoR application ({url})",
                    v.HTTP_HEADER_X_RUNTIME,
                )
            )

        if "X-Backend-Server" in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f'X-Backend-Server Header Present: {headers["X-Backend-Server"]} ({url})',
                    v.HTTP_HEADER_X_BACKEND_SERVER,
                )
            )

        if "Via" in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f'Via Header Present: #{headers["Via"]} ({url})',
                    v.HTTP_HEADER_VIA,
                )
            )

        if "X-Frame-Options" in headers:
            if "allow" in str(headers["X-Frame-Options"]).lower():
                results.append(
                    Result.from_evidence(
                        Evidence.from_response(res),
                        f'X-Frame-Options Header: {headers["X-Frame-Options"]} ({url})',
                        v.HTTP_HEADER_X_FRAME_OPTIONS_ALLOW,
                    )
                )
        else:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f"X-Frame-Options Header Not Present ({url})",
                    v.HTTP_HEADER_X_FRAME_OPTIONS_MISSING,
                )
            )

        if "X-Content-Type-Options" not in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f"X-Content-Type-Options Header Not Present ({url})",
                    v.HTTP_HEADER_X_CONTENT_TYPE_OPTIONS_MISSING,
                )
            )

        if "Content-Security-Policy" not in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f"Content-Security-Policy Header Not Present ({url})",
                    v.HTTP_HEADER_CONTENT_SECURITY_POLICY_MISSING,
                )
            )

        if "Referrer-Policy" not in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f"Referrer-Policy Header Not Present ({url})",
                    v.HTTP_HEADER_REFERRER_POLICY_MISSING,
                )
            )

        if "Feature-Policy" not in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f"Feature-Policy Header Not Present ({url})",
                    v.HTTP_HEADER_FEATURE_POLICY_MISSING,
                )
            )

        if "Access-Control-Allow-Origin" in headers:
            if headers["Access-Control-Allow-Origin"] == "*":
                results.append(
                    Result.from_evidence(
                        Evidence.from_response(res),
                        f"Access-Control-Allow-Origin: Unrestricted ({url})",
                        v.HTTP_HEADER_CORS_ACAO_UNRESTRICTED,
                    )
                )

        if "Strict-Transport-Security" not in headers:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    f"Strict-Transport-Security Header Not Present ({url})",
                    v.HTTP_HEADER_HSTS_MISSING,
                )
            )

        if "Server" in headers:
            results += get_server_banner_issues(headers["Server"], raw, url, headers)
    except Exception:
        output.debug_exception()

    return results


def get_server_banner_issues(
    server: str, raw: str, url: str, headers: Dict
) -> List[Result]:
    results: List[Result] = []

    results += apache_httpd.check_banner(server, raw, url)
    results += nginx.check_banner(server, raw, url)
    results += iis.check_version(server, raw, url, headers)
    results += python.check_banner(server, raw, url)

    return results


def check_propfind(url: str) -> List[Result]:
    results: List[Result] = []

    res = network.http_custom("PROPFIND", url)
    body = res.text

    if res.status_code <= 400 and len(body) > 0:
        if "Content-Type" in res.headers and "text/xml" in res.headers["Content-Type"]:
            results.append(
                Result.from_evidence(
                    Evidence.from_response(res),
                    "Possible Info Disclosure: PROPFIND Enabled",
                    v.HTTP_PROPFIND_ENABLED,
                )
            )

    results += response_scanner.check_response(url, res)

    return results


def check_trace(url: str) -> List[Result]:
    results: List[Result] = []

    res = network.http_custom("TRACE", url)
    body = res.text

    if res.status_code == 200 and "TRACE / HTTP/1.1" in body:
        results.append(
            Result.from_evidence(
                Evidence.from_response(res), "HTTP TRACE Enabled", v.HTTP_TRACE_ENABLED
            )
        )

    results += response_scanner.check_response(url, res)

    return results


def check_options(url: str) -> List[Result]:
    results: List[Result] = []

    res = network.http_options(url)

    if "Allow" in res.headers:
        results.append(
            Result.from_evidence(
                Evidence.from_response(res),
                f"Allow HTTP Verbs (OPTIONS): {res.headers['Allow']}",
                v.HTTP_OPTIONS_ALLOW,
            )
        )

    if "Public" in res.headers:
        results.append(
            Result.from_evidence(
                Evidence.from_response(res),
                f"Public HTTP Verbs (OPTIONS): {res.headers['Public']}",
                v.HTTP_OPTIONS_PUBLIC,
            )
        )

    results += response_scanner.check_response(url, res)

    return results


def check_local_ip_disclosure(session: Session) -> List[Result]:
    def _send_http_10_get(
        con: Union[SslConnection, socket.socket]
    ) -> Tuple[str, HTTPResponse]:
        req = (
            "HEAD / HTTP/1.0\r\n"
            "User-Agent: {user_agent}\r\n"
            "Accept: */*\r\n\r\n".format(user_agent=network.YAWAST_UA)
        )

        if type(con) is SslConnection:
            con.ssl_client.write(req.encode("utf_8"))

            res = http_response_parser.HttpResponseParser.parse_from_ssl_connection(
                con.ssl_client
            )
        else:
            con.sendall(req.encode("utf_8"))

            res = http_response_parser.HttpResponseParser.parse_from_socket(con)

        return req, res

    def _resp_to_str(res: HTTPResponse) -> str:
        ver = "1.1" if res.version == 11 else "1.0"
        body = f"HTTP/{ver} {res.code} {res.reason}\r\n"
        for k, v in res.headers.items():
            body += f"{k}: {v}\r\n"

        return body

    def _get_ip(res: HTTPResponse) -> Union[str, None]:
        loc = res.getheader("Location")
        if loc is not None:
            # it's a redirect, check to see if there's an IP in it
            parsed = urlparse(loc)
            domain = utils.get_domain(parsed.netloc)

            if utils.is_ip(domain):
                # it's an IP, now, is it private?
                if utils.is_private_ip(domain):
                    return domain
                else:
                    return None

        return None

    def _get_result(client, prt):
        req, resp = _send_http_10_get(client)
        ip = _get_ip(resp)

        if ip is not None:
            results.append(
                Result(
                    f"Private IP Found: {ip} via HTTP 1.0 Redirect",
                    v.SERVER_INT_IP_EXP_HTTP10,
                    session.url,
                    {
                        "request": req,
                        "response": _resp_to_str(resp),
                        "ip": {ip},
                        "port": prt,
                    },
                )
            )

    results: List[Result] = []

    if session.url_parsed.scheme == "https":
        conn_tester = server_connectivity_tester.ServerConnectivityTester(
            hostname=session.domain, port=utils.get_port(session.url)
        )

        server_info = conn_tester.perform()

        conn = ssl_connection_configurator.SslConnectionConfigurator.get_connection(
            ssl_version=OpenSslVersionEnum.SSLV23,
            server_info=server_info,
            should_ignore_client_auth=True,
            ssl_verify_locations=None,
            should_use_legacy_openssl=False,
        )

        conn.connect()

        try:
            _get_result(conn, utils.get_port(session.url))
        except Exception:
            output.debug_exception()

    if session.supports_http:
        url = session.get_http_url()
        port = utils.get_port(url)
        conn = socket.socket()
        conn.connect((utils.get_domain(url), port))

        try:
            _get_result(conn, port)
        except Exception:
            output.debug_exception()

    return results


def get_cookie_issues(res: Response, url: str) -> List[Result]:
    if "Set-Cookie" in res.headers:
        cookies = res.raw.headers.getlist("Set-Cookie")

        return _get_cookie_issues(cookies, url, res)
    else:
        return []


def _get_cookie_issues(cookies: List[str], url: str, res: Response) -> List[Result]:
    global _checked_cookies

    # setup the checked list
    if v.COOKIE_MISSING_SECURE_FLAG not in _checked_cookies:
        _checked_cookies[v.COOKIE_MISSING_SECURE_FLAG] = []
    if v.COOKIE_INVALID_SECURE_FLAG not in _checked_cookies:
        _checked_cookies[v.COOKIE_INVALID_SECURE_FLAG] = []
    if v.COOKIE_MISSING_HTTPONLY_FLAG not in _checked_cookies:
        _checked_cookies[v.COOKIE_MISSING_HTTPONLY_FLAG] = []
    if v.COOKIE_MISSING_SAMESITE_FLAG not in _checked_cookies:
        _checked_cookies[v.COOKIE_MISSING_SAMESITE_FLAG] = []
    if v.COOKIE_WITH_SAMESITE_NONE_FLAG not in _checked_cookies:
        _checked_cookies[v.COOKIE_WITH_SAMESITE_NONE_FLAG] = []
    if v.COOKIE_INVALID_SAMESITE_NONE_FLAG not in _checked_cookies:
        _checked_cookies[v.COOKIE_INVALID_SAMESITE_NONE_FLAG] = []
    if v.COOKIE_BIGIP_IP_DISCLOSURE not in _checked_cookies:
        _checked_cookies[v.COOKIE_BIGIP_IP_DISCLOSURE] = []

    results: List[Result] = []

    try:
        parsed = urlparse(url)

        for cookie in cookies:
            comp = cookie.split(";")

            # get the name
            name = comp[0].split("=")[0]

            # get the value
            value = comp[0].split("=")[1]

            # normalize the components
            comp = list(map(str.strip, comp))  # trim the string to clear the spaces
            comp = list(
                map(str.lower, comp)  # make it all lowercase, to simplify checks
            )

            # check for BigIP IP Disclosure
            if "BIGip" in name:
                if name not in _checked_cookies[v.COOKIE_BIGIP_IP_DISCLOSURE]:
                    _checked_cookies[v.COOKIE_BIGIP_IP_DISCLOSURE].append(name)
                    decoded = _decode_big_ip_cookie(value)

                    if decoded is not None:
                        results.append(
                            Result.from_evidence(
                                Evidence.from_response(res, {"cookie": name}),
                                f"Big-IP Internal IP Address Disclosure: {name}: {decoded}",
                                v.COOKIE_BIGIP_IP_DISCLOSURE,
                            )
                        )

            # check Secure flag
            if "secure" not in comp and parsed.scheme == "https":
                if name not in _checked_cookies[v.COOKIE_MISSING_SECURE_FLAG]:
                    results.append(
                        Result.from_evidence(
                            Evidence.from_response(res, {"cookie": name}),
                            f"Cookie Missing Secure Flag: {cookie}",
                            v.COOKIE_MISSING_SECURE_FLAG,
                        )
                    )

                    _checked_cookies[v.COOKIE_MISSING_SECURE_FLAG].append(name)
            elif "secure" in comp and parsed.scheme == "http":
                # secure flag over HTTP is invalid
                if name not in _checked_cookies[v.COOKIE_INVALID_SECURE_FLAG]:
                    results.append(
                        Result.from_evidence(
                            Evidence.from_response(res, {"cookie": name}),
                            f"Cookie Secure Flag Invalid (over HTTP): {cookie}",
                            v.COOKIE_INVALID_SECURE_FLAG,
                        )
                    )

                    _checked_cookies[v.COOKIE_INVALID_SECURE_FLAG].append(name)

            # check HttpOnly flag
            if "httponly" not in comp:
                if name not in _checked_cookies[v.COOKIE_MISSING_HTTPONLY_FLAG]:
                    results.append(
                        Result.from_evidence(
                            Evidence.from_response(res, {"cookie": name}),
                            f"Cookie Missing HttpOnly Flag: {cookie}",
                            v.COOKIE_MISSING_HTTPONLY_FLAG,
                        )
                    )

                    _checked_cookies[v.COOKIE_MISSING_HTTPONLY_FLAG].append(name)

            # check SameSite flag
            if (
                "samesite=lax" not in comp
                and "samesite=strict" not in comp
                and "samesite=none" not in comp
            ):
                if name not in _checked_cookies[v.COOKIE_MISSING_SAMESITE_FLAG]:
                    results.append(
                        Result.from_evidence(
                            Evidence.from_response(res, {"cookie": name}),
                            f"Cookie Missing SameSite Flag: {cookie}",
                            v.COOKIE_MISSING_SAMESITE_FLAG,
                        )
                    )

                    _checked_cookies[v.COOKIE_MISSING_SAMESITE_FLAG].append(name)

            # check SameSite=None flag
            if "samesite=none" in comp:
                if "secure" in comp:
                    if name not in _checked_cookies[v.COOKIE_WITH_SAMESITE_NONE_FLAG]:
                        results.append(
                            Result.from_evidence(
                                Evidence.from_response(res, {"cookie": name}),
                                f"Cookie With SameSite=None Flag: {cookie}",
                                v.COOKIE_WITH_SAMESITE_NONE_FLAG,
                            )
                        )

                        _checked_cookies[v.COOKIE_WITH_SAMESITE_NONE_FLAG].append(name)
                else:
                    if (
                        name
                        not in _checked_cookies[v.COOKIE_INVALID_SAMESITE_NONE_FLAG]
                    ):
                        results.append(
                            Result.from_evidence(
                                Evidence.from_response(res, {"cookie": name}),
                                f"Cookie SameSite=None Flag Invalid (without Secure flag): {cookie}",
                                v.COOKIE_INVALID_SAMESITE_NONE_FLAG,
                            )
                        )

                        _checked_cookies[v.COOKIE_INVALID_SAMESITE_NONE_FLAG].append(
                            name
                        )
    except Exception:
        output.debug_exception()

    return results


def _decode_big_ip_cookie(value: str) -> Union[str, None]:
    def _swap_endianness(val, bits: int = 32):
        if bits == 32:
            return struct.unpack("<I", struct.pack(">I", val))[0]
        elif bits == 16:
            return struct.unpack("<H", struct.pack(">H", val))[0]

    # regex copied from: https://github.com/rapid7/metasploit-framework
    #   /blob/6300758c46464ff5488bc49bc326ebbb1df46321
    #   /modules/auxiliary/gather/f5_bigip_cookie_disclosure.rb
    # License: BSD
    # Copyright: 2006-2018, Rapid7, Inc.
    value_pattern = (
        r"(((?:\d+\.){2}\d+)|(rd\d+o0{20}f{4}\w+o\d{1,5})|"
        r"(vi([a-f0-9]{32})\.(\d{1,5}))|"
        r"(rd\d+o([a-f0-9]{32})o(\d{1,5})))(?:$|,|;|\s)"
    )

    if re.match(value_pattern, value):
        # it fits the pattern
        if re.search(r"(\d{8,10})\.(\d{1,5})\.", value):
            # BIGipServerWEB=2263487148.3013.0000 - IPv4
            comps = value.split(".")
            host = socket.inet_ntop(
                socket.AF_INET,
                _swap_endianness(int(comps[0])).to_bytes(4, byteorder="big"),
            )
            port = _swap_endianness(int(comps[1]), 16)

            if utils.is_private_ip(host):
                return f"{host}:{port}"
        elif re.search(r"rd\d+o0{20}f{4}([a-f0-9]{8})o(\d{1,5})", value):
            # BIGipServerWEB=rd5o00000000000000000000ffffc0000201o80 - IPv4
            comps = value.split("o")
            host = socket.inet_ntop(
                socket.AF_INET, int(comps[1][24:32], 16).to_bytes(4, byteorder="big")
            )
            port = int(comps[2])

            if utils.is_private_ip(host):
                return f"{host}:{port}"
        elif re.search(r"vi([a-f0-9]{32})\.(\d{1,5})", value):
            # BIGipServerWEB=vi20010112000000000000000000000030.20480 - IPv6
            comps = value.split(".")
            comps[0] = comps[0].replace("vi", "", 1)
            host = socket.inet_ntop(
                socket.AF_INET6, int(comps[0], 16).to_bytes(16, byteorder="big")
            )
            port = _swap_endianness(int(comps[1]), 16)

            if utils.is_private_ip(host):
                return f"{host}:{port}"
        elif re.search(r"rd\d+o([a-f0-9]{32})o(\d{1,5})", value):
            # BIGipServerWEB=rd3o20010112000000000000000000000030o80 - IPv6
            comps = value.split("o")
            host = socket.inet_ntop(
                socket.AF_INET6, int(comps[1], 16).to_bytes(16, byteorder="big")
            )
            port = int(comps[2])

            if utils.is_private_ip(host):
                return f"{host}:{port}"

    return None

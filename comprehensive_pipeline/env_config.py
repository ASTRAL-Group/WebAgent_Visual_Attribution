# ===== Minimal compatible config for local static-variants =====

# 1) 基础：你的静态站点主页
HOMEPAGE_URL = "http://127.0.0.1:8877/index.html"

# 2) 所有站点 URL（即便用不到也给一个可用 URL，避免断言/导入失败）
SITE_URLS = {
    "Homepage": HOMEPAGE_URL,
    "Shopping": HOMEPAGE_URL,
    "Shopping Admin": HOMEPAGE_URL,
    "Gitlab": HOMEPAGE_URL,
    "Wikipedia": HOMEPAGE_URL,
    "Map": HOMEPAGE_URL,
    "Reddit": HOMEPAGE_URL,
}

# 3) 一些历史代码会直接 import 这些单个常量；做个映射即可
SHOPPING_URL = SITE_URLS.get("Shopping")
SHOPPING_ADMIN_URL = SITE_URLS.get("Shopping Admin")
GITLAB_URL = SITE_URLS.get("Gitlab")
WIKIPEDIA_URL = SITE_URLS.get("Wikipedia")
MAP_URL = SITE_URLS.get("Map")
REDDIT_URL = SITE_URLS.get("Reddit")

# 4) 允许访问的域（WebArena 有跨域/白名单检查）
ALLOWED_ORIGINS = {
    "127.0.0.1:8877",
    "localhost:8877",
}

# 5) 一些版本会 import 这个，用于启动前 sleep 秒数
STARTUP_SLEEP = 0

# 6) 账号占位（就算不用登录，也要提供结构避免 ImportError/KeyError）
#    结构尽量包含常见的 key，保证下游健壮
ACCOUNTS = {
    "shopping": {
        "user": {"username": "", "password": ""},
        "admin": {"username": "", "password": ""},
    },
    "shopping_admin": {
        "admin": {"username": "", "password": ""},
    },
    "gitlab": {
        "user": {"username": "", "password": ""},
        "admin": {"username": "", "password": ""},
    },
    "reddit": {
        "user": {"username": "", "password": ""},
    },
    "wikipedia": {
        "user": {"username": "", "password": ""},
    },
    "map": {
        "user": {"username": "", "password": ""},
    },
    "homepage": {
        "user": {"username": "", "password": ""},
    },
}

# 7) 某些版本会 import 这个开关
ALLOW_HEADLESS = True
# ===== Compatibility layer for evaluation_harness =====

# 你的静态站点主页
HOMEPAGE_URL = "http://127.0.0.1:8877/index.html"

# 统一的站点映射（不用的也指向本地，避免 assert/import 失败）
SITE_URLS = {
    "Homepage": HOMEPAGE_URL,
    "Shopping": HOMEPAGE_URL,
    "Shopping Admin": HOMEPAGE_URL,
    "Gitlab": HOMEPAGE_URL,
    "Wikipedia": HOMEPAGE_URL,
    "Map": HOMEPAGE_URL,
    "Reddit": HOMEPAGE_URL,
}

# 许多版本的 harness 会直接 import 这些常量名称
HOMEPAGE = SITE_URLS["Homepage"]
SHOPPING = SITE_URLS["Shopping"]
SHOPPING_ADMIN = SITE_URLS["Shopping Admin"]
GITLAB = SITE_URLS["Gitlab"]
WIKIPEDIA = SITE_URLS["Wikipedia"]
MAP = SITE_URLS["Map"]
REDDIT = SITE_URLS["Reddit"]

# 允许跨域/白名单
ALLOWED_ORIGINS = {
    "127.0.0.1:8877",
    "localhost:8877",
}

# 账号占位（不用登录也要提供结构，避免 KeyError）
ACCOUNTS = {
    "shopping": {
        "user": {"username": "", "password": ""},
        "admin": {"username": "", "password": ""},
    },
    "shopping_admin": {"admin": {"username": "", "password": ""}},
    "gitlab": {
        "user": {"username": "", "password": ""},
        "admin": {"username": "", "password": ""},
    },
    "reddit": {"user": {"username": "", "password": ""}},
    "wikipedia": {"user": {"username": "", "password": ""}},
    "map": {"user": {"username": "", "password": ""}},
    "homepage": {"user": {"username": "", "password": ""}},
}

# 其他版本可能 import 的占位
PROXIES = {}
USER_AGENTS = {}
DISABLE_CSP = True
ALLOW_HEADLESS = True
STARTUP_SLEEP = 0


# # websites domain
# import os

# REDDIT = os.environ.get("REDDIT", "")
# SHOPPING = os.environ.get("SHOPPING", "")
# SHOPPING_ADMIN = os.environ.get("SHOPPING_ADMIN", "")
# GITLAB = os.environ.get("GITLAB", "")
# WIKIPEDIA = os.environ.get("WIKIPEDIA", "")
# MAP = os.environ.get("MAP", "")
# HOMEPAGE = os.environ.get("HOMEPAGE", "")

# assert (
#     REDDIT
#     and SHOPPING
#     and SHOPPING_ADMIN
#     and GITLAB
#     and WIKIPEDIA
#     and MAP
#     and HOMEPAGE
# ), (
#     f"Please setup the URLs to each site. Current: \n"
#     + f"Reddit: {REDDIT}\n"
#     + f"Shopping: {SHOPPING}\n"
#     + f"Shopping Admin: {SHOPPING_ADMIN}\n"
#     + f"Gitlab: {GITLAB}\n"
#     + f"Wikipedia: {WIKIPEDIA}\n"
#     + f"Map: {MAP}\n"
#     + f"Homepage: {HOMEPAGE}\n"
# )


# ACCOUNTS = {
#     "reddit": {"username": "MarvelsGrantMan136", "password": "test1234"},
#     "gitlab": {"username": "byteblaze", "password": "hello1234"},
#     "shopping": {
#         "username": "emma.lopez@gmail.com",
#         "password": "Password.123",
#     },
#     "shopping_admin": {"username": "admin", "password": "admin1234"},
#     "shopping_site_admin": {"username": "admin", "password": "admin1234"},
# }

# URL_MAPPINGS = {
#     REDDIT: "http://reddit.com",
#     SHOPPING: "http://onestopmarket.com",
#     SHOPPING_ADMIN: "http://luma.com/admin",
#     GITLAB: "http://gitlab.com",
#     WIKIPEDIA: "http://wikipedia.org",
#     MAP: "http://openstreetmap.org",
#     HOMEPAGE: "http://homepage.com",
# }

# HOMEPAGE = os.environ.get("HOMEPAGE", "http://127.0.0.1:8877/index.html")

# ALLOWED_ORIGINS = {
#     "127.0.0.1:8877", "localhost:8877",
#     # 其余已有站点域名...
# }

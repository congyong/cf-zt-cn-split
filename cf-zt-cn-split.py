import requests
import os
import re

CF_API_TOKEN = os.getenv("CF_API_TOKEN")
ACCOUNT_ID   = os.getenv("CF_ACCOUNT_ID")
PROFILE_ID   = os.getenv("CF_PROFILE_ID", "")
MODE         = os.getenv("MODE", "exclude")  # exclude=CN直连 | include=只有CN走WARP

if not all([CF_API_TOKEN, ACCOUNT_ID]):
    raise ValueError("缺少环境变量！请在 GitHub Secrets 设置 CF_API_TOKEN、CF_ACCOUNT_ID")

HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

MAX_RULES       = 4000
TARGET_DOMAIN_N = 200  # 期望域名条数，剩余配额给 IP

# 合法域名正则：只保留标准域名格式，过滤脏数据
VALID_DOMAIN_RE = re.compile(r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$')

# 域名：Loyalsoldier 精选直连域名
DOMAIN_URL = "https://raw.githubusercontent.com/congyong/cf-zt-cn-split/refs/heads/master/direct.txt"

# IP：GeoIP2-CN
IP_URL = "https://raw.githubusercontent.com/soffchen/GeoIP2-CN/release/CN-ip-cidr.txt"

# ==============================================
# Cloudflare 默认内网保留IP段（放在最前面）
# ==============================================
DEFAULT_INTERNAL_IPS = [
    "10.0.0.0/8",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "224.0.0.0/24",
    "240.0.0.0/4",
    "255.255.255.255/32",
    "fe80::/10",
    "fd00::/8",
    "ff01::/16",
    "ff02::/16",
    "ff03::/16",
    "ff04::/16",
    "ff05::/16"
]

def get_cn_cidrs():
    """从GeoIP2-CN 拉取聚合的 CN CIDR 列表"""
    r = requests.get(IP_URL, timeout=30)
    r.raise_for_status()
    cidrs = [line.strip() for line in r.text.splitlines() if line.strip() and not line.startswith('#')]
    print(f"   IP 数据源获取到 {len(cidrs)} 条 CIDR")
    return cidrs


def get_cn_domains():
    """从 Loyalsoldier/surge-rules 拉取精选 CN 直连域名列表，过滤非法格式"""
    r = requests.get(DOMAIN_URL, timeout=30)
    r.raise_for_status()
    domains = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # 兼容 DOMAIN-SUFFIX,xxx 格式
        if line.startswith('DOMAIN-SUFFIX,'):
            line = line.replace('DOMAIN-SUFFIX,', '').strip()
        # 去掉前导点（如 .baidu.com → baidu.com）
        line = line.lstrip('.')
        # 只保留合法域名格式，过滤脏数据
        if line and VALID_DOMAIN_RE.match(line):
            domains.append(f"*.{line}")
    unique = list(set(domains))
    print(f"   域名数据源获取到 {len(unique)} 条域名（已过滤非法格式）")
    return unique


def update_split_tunnels(cidrs, domains):
    # 动态分配配额：域名取 TARGET_DOMAIN_N 条，剩余给 IP
    max_domains = min(TARGET_DOMAIN_N, len(domains))
    max_ips     = min(MAX_RULES - max_domains - len(DEFAULT_INTERNAL_IPS), len(cidrs))

    # 顺序：内网保留IP → 域名 → 国内IP
    internal_entries = [{"address": ip, "description": "LAN/Internal IP"} for ip in DEFAULT_INTERNAL_IPS]
    domain_entries = [{"host": d, "description": "CN Domain"} for d in domains[:max_domains]]
    ip_entries = [{"address": cidr, "description": "CN IP"} for cidr in cidrs[:max_ips]]
    
    # 最终路由表（内网IP永远在最前面）
    routes = internal_entries + domain_entries + ip_entries

    # 逐条打印 domain_entries
    print("\n===== 正在打印 domain_entries 列表 =====")
    for index, entry in enumerate(domain_entries, 1):
        print(f"第 {index} 条 | host: {entry['host']:<30} | description: {entry['description']}")

    print(f"\n   内网IP：{len(internal_entries)} 条 | 域名：{len(domain_entries)} 条 | 国内IP：{len(ip_entries)} 条 | 合计：{len(routes)} 条")

    if len(routes) > MAX_RULES:
        print(f"⚠️  规则总数超出限制，已截断至 {MAX_RULES} 条")
        routes = routes[:MAX_RULES]

    if PROFILE_ID:
        url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/devices/policy/{PROFILE_ID}/{MODE}"
    else:
        url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/devices/policy/{MODE}"

    resp = requests.put(url, json=routes, headers=HEADERS)
    if resp.status_code in (200, 204):
        print(f"\n✅ 同步成功！{len(routes)} 条路由 | Mode: {MODE}")
    else:
        print(f"\n❌ 失败 {resp.status_code}: {resp.text}")
        resp.raise_for_status()


if __name__ == "__main__":
    print("🔄 拉取最新 CN geo 数据...")
    cidrs   = get_cn_cidrs()
    domains = get_cn_domains()
    update_split_tunnels(cidrs, domains)

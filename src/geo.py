"""Tiny, dependency-free location classifier: India first, then remote, then abroad."""
from __future__ import annotations

import re
from dataclasses import dataclass

INDIA_TERMS = (
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "mumbai", "delhi", "new delhi",
    "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad", "kochi", "trivandrum", "thiruvananthapuram",
    "coimbatore", "mysore", "mysuru", "mangalore", "jaipur", "indore", "chandigarh", "karnataka",
    "maharashtra", "telangana", "tamil nadu", "kerala", "haryana",
)
REMOTE_TERMS = ("remote", "anywhere", "worldwide", "global", "work from home", "wfh", "distributed", "fully remote")
# Remote scopes that an India-based person can actually work from.
REMOTE_OK_SCOPES = ("india", "apac", "asia", "worldwide", "anywhere", "global", "emea", "international")

COUNTRY_TERMS: dict[str, tuple[str, ...]] = {
    "usa": ("united states", "usa", "u.s.", "new york", "san francisco", "seattle", "austin", "boston",
            "chicago", "los angeles", "denver", "atlanta", "san jose", "mountain view", "palo alto"),
    "canada": ("canada", "toronto", "vancouver", "montreal", "ottawa", "calgary", "waterloo"),
    "uk": ("united kingdom", "uk", "england", "scotland", "london", "manchester", "edinburgh", "cambridge"),
    "ireland": ("ireland", "dublin", "cork"),
    "germany": ("germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne"),
    "netherlands": ("netherlands", "amsterdam", "rotterdam", "utrecht", "eindhoven", "the hague"),
    "poland": ("poland", "warsaw", "krakow", "kraków", "wroclaw", "gdansk"),
    "sweden": ("sweden", "stockholm", "gothenburg"),
    "finland": ("finland", "helsinki"),
    "denmark": ("denmark", "copenhagen"),
    "norway": ("norway", "oslo"),
    "spain": ("spain", "madrid", "barcelona"),
    "portugal": ("portugal", "lisbon", "porto"),
    "france": ("france", "paris"),
    "switzerland": ("switzerland", "zurich", "zürich", "geneva"),
    "austria": ("austria", "vienna"),
    "czechia": ("czech", "czechia", "prague", "brno"),
    "estonia": ("estonia", "tallinn"),
    "uae": ("united arab emirates", "uae", "dubai", "abu dhabi"),
    "saudi arabia": ("saudi", "riyadh", "jeddah"),
    "qatar": ("qatar", "doha"),
    "singapore": ("singapore",),
    "malaysia": ("malaysia", "kuala lumpur"),
    "japan": ("japan", "tokyo", "osaka"),
    "australia": ("australia", "sydney", "melbourne", "brisbane", "perth"),
    "new zealand": ("new zealand", "auckland", "wellington"),
    "israel": ("israel", "tel aviv"),
    "belgium": ("belgium", "brussels", "antwerp"),
    "italy": ("italy", "milan", "rome"),
    "romania": ("romania", "bucharest", "cluj"),
    "hungary": ("hungary", "budapest"),
    "greece": ("greece", "athens"),
    "turkey": ("turkey", "türkiye", "istanbul"),
    "ukraine": ("ukraine", "kyiv", "kiev", "lviv"),
    "lithuania": ("lithuania", "vilnius"),
    "luxembourg": ("luxembourg",),
    "brazil": ("brazil", "brasil", "são paulo", "sao paulo", "rio de janeiro"),
    "mexico": ("mexico", "méxico", "monterrey", "guadalajara"),
    "argentina": ("argentina", "buenos aires"),
    "colombia": ("colombia", "bogota", "bogotá", "medellin"),
    "chile": ("chile", "santiago"),
    "costa rica": ("costa rica", "san josé"),
    "south africa": ("south africa", "cape town", "johannesburg"),
    "egypt": ("egypt", "cairo"),
    "nigeria": ("nigeria", "lagos"),
    "kenya": ("kenya", "nairobi"),
    "philippines": ("philippines", "manila", "taguig", "cebu"),
    "vietnam": ("vietnam", "viet nam", "ho chi minh", "hanoi"),
    "indonesia": ("indonesia", "jakarta"),
    "thailand": ("thailand", "bangkok"),
    "china": ("china", "shanghai", "beijing", "shenzhen", "dalian"),
    "hong kong": ("hong kong",),
    "taiwan": ("taiwan", "taipei"),
    "south korea": ("korea", "seoul"),
    "pakistan": ("pakistan", "karachi", "lahore", "islamabad"),
    "bangladesh": ("bangladesh", "dhaka"),
    "sri lanka": ("sri lanka", "colombo"),
    "bulgaria": ("bulgaria", "sofia"),
    "croatia": ("croatia", "zagreb"),
    "cyprus": ("cyprus", "limassol", "nicosia"),
    "latvia": ("latvia", "riga"),
    "malta": ("malta",),
    "slovakia": ("slovakia", "bratislava"),
    "slovenia": ("slovenia", "ljubljana"),
    "serbia": ("serbia", "belgrade"),
    "iceland": ("iceland", "reykjavik"),
    "bahrain": ("bahrain", "manama"),
    "kuwait": ("kuwait",),
    "oman": ("oman", "muscat"),
    "jordan": ("jordan", "amman"),
    "morocco": ("morocco", "casablanca", "rabat"),
    "ecuador": ("ecuador", "quito"),
    "panama": ("panama",),
    "peru": ("peru", "lima"),
    "uruguay": ("uruguay", "montevideo"),
    "venezuela": ("venezuela", "caracas"),
    "nepal": ("nepal", "kathmandu"),
}
# Indeed's country names -> our canonical names.
COUNTRY_ALIASES = {"united arab emirates": "uae", "czech republic": "czechia", "türkiye": "turkey"}
_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
    "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
_US_STATE_RE = re.compile(r",\s*(" + "|".join(_US_STATES) + r")\b")


def _has(term: str, text: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None


@dataclass(frozen=True)
class Geo:
    region: str  # india | remote | abroad | unknown
    country: str


def detect_country(location: str) -> str:
    low = location.lower()
    if any(_has(t, low) for t in INDIA_TERMS):
        return "india"
    for country, terms in COUNTRY_TERMS.items():
        if any(_has(t, low) for t in terms):
            return country
    if _US_STATE_RE.search(location) or re.search(r"(?<![A-Za-z])US(?![A-Za-z])", location):
        return "usa"
    return ""


def classify(location: str, remote_flag: bool = False) -> Geo:
    loc = location or ""
    low = loc.lower()
    country = detect_country(loc)
    is_remote = remote_flag or any(_has(t, low) for t in REMOTE_TERMS)
    if country == "india":
        return Geo("india", "india")
    if is_remote:
        # "Remote - US" is not workable from India; "Remote (APAC)" or plain "Remote" is.
        if not country or any(_has(s, low) for s in REMOTE_OK_SCOPES):
            return Geo("remote", country)
        return Geo("abroad", country)
    if country:
        return Geo("abroad", country)
    return Geo("unknown", "")

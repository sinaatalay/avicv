"""Best-effort location parsing for US/Canada eligibility: a heuristic, not the truth.

Input: raw location strings plus any structured country hints a provider gives (Lever
`country`, SmartRecruiters `location.country`, Ashby `addressCountry`, Himalayas
`locationRestrictions`). Output: ISO 3166-1 alpha-2 countries, ISO 3166-2-style regions
for the US and Canada ("US-CA", "CA-AB"), and a bucket:

- `us_ca`: at least one location is in the US or Canada (incl. "North America" remote)
- `remote_unspecified`: remote, with no country information
- `other`: only other countries or regions (EMEA, APAC, ...)
- `unknown`: nothing parseable
"""

import re
from collections.abc import Mapping
from functools import lru_cache

# fmt: off
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN",
    "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
}

CA_PROVINCES = {
    "alberta": "AB", "british columbia": "BC", "manitoba": "MB", "new brunswick": "NB",
    "newfoundland": "NL", "newfoundland and labrador": "NL", "nova scotia": "NS",
    "ontario": "ON", "prince edward island": "PE", "quebec": "QC", "québec": "QC",
    "saskatchewan": "SK", "northwest territories": "NT", "nunavut": "NU", "yukon": "YT",
}

# Big tech-hiring cities. Ambiguous names (London, Cambridge, Victoria, Paris) are
# absent unless qualified, e.g. "victoria, bc".
CITIES = {
    "san francisco": ("US", "CA"), "sf": ("US", "CA"), "bay area": ("US", "CA"),
    "palo alto": ("US", "CA"), "mountain view": ("US", "CA"), "menlo park": ("US", "CA"),
    "sunnyvale": ("US", "CA"), "san jose": ("US", "CA"), "san mateo": ("US", "CA"),
    "santa clara": ("US", "CA"), "redwood city": ("US", "CA"), "oakland": ("US", "CA"),
    "los angeles": ("US", "CA"), "san diego": ("US", "CA"), "irvine": ("US", "CA"),
    "costa mesa": ("US", "CA"), "santa monica": ("US", "CA"), "foster city": ("US", "CA"),
    "pleasanton": ("US", "CA"), "milpitas": ("US", "CA"), "cupertino": ("US", "CA"),
    "emeryville": ("US", "CA"), "south san francisco": ("US", "CA"),
    "new york city": ("US", "NY"), "nyc": ("US", "NY"), "brooklyn": ("US", "NY"),
    "manhattan": ("US", "NY"), "seattle": ("US", "WA"), "bellevue": ("US", "WA"),
    "redmond": ("US", "WA"), "kirkland": ("US", "WA"), "austin": ("US", "TX"),
    "dallas": ("US", "TX"), "houston": ("US", "TX"), "san antonio": ("US", "TX"),
    "plano": ("US", "TX"), "boston": ("US", "MA"), "somerville": ("US", "MA"),
    "chicago": ("US", "IL"), "denver": ("US", "CO"), "boulder": ("US", "CO"),
    "atlanta": ("US", "GA"), "miami": ("US", "FL"), "tampa": ("US", "FL"),
    "orlando": ("US", "FL"), "phoenix": ("US", "AZ"), "scottsdale": ("US", "AZ"),
    "tempe": ("US", "AZ"), "salt lake city": ("US", "UT"), "lehi": ("US", "UT"),
    "draper": ("US", "UT"), "portland": ("US", None), "pittsburgh": ("US", "PA"),
    "philadelphia": ("US", "PA"), "minneapolis": ("US", "MN"), "detroit": ("US", "MI"),
    "ann arbor": ("US", "MI"), "nashville": ("US", "TN"), "raleigh": ("US", "NC"),
    "durham": ("US", "NC"), "charlotte": ("US", "NC"), "columbus": ("US", "OH"),
    "reston": ("US", "VA"), "arlington": ("US", "VA"), "mclean": ("US", "VA"),
    "herndon": ("US", "VA"), "baltimore": ("US", "MD"), "st. louis": ("US", "MO"),
    "kansas city": ("US", None), "las vegas": ("US", "NV"), "madison": ("US", "WI"),
    "jersey city": ("US", "NJ"), "hoboken": ("US", "NJ"), "princeton": ("US", "NJ"),
    "stamford": ("US", "CT"), "providence": ("US", "RI"), "sacramento": ("US", "CA"),
    "el segundo": ("US", "CA"), "hawthorne": ("US", "CA"), "starbase": ("US", "TX"),
    "cape canaveral": ("US", "FL"), "huntsville": ("US", "AL"),
    "washington, d.c.": ("US", "DC"), "toronto": ("CA", "ON"), "ottawa": ("CA", "ON"),
    "waterloo": ("CA", "ON"), "kitchener": ("CA", "ON"), "mississauga": ("CA", "ON"),
    "markham": ("CA", "ON"), "hamilton, on": ("CA", "ON"), "vancouver": ("CA", "BC"),
    "burnaby": ("CA", "BC"), "surrey, bc": ("CA", "BC"), "victoria, bc": ("CA", "BC"),
    "montreal": ("CA", "QC"), "montréal": ("CA", "QC"), "quebec city": ("CA", "QC"),
    "québec city": ("CA", "QC"), "calgary": ("CA", "AB"), "edmonton": ("CA", "AB"),
    "winnipeg": ("CA", "MB"), "halifax": ("CA", "NS"), "saskatoon": ("CA", "SK"),
    "regina": ("CA", "SK"), "fredericton": ("CA", "NB"), "st. john's": ("CA", "NL"),
}

COUNTRIES = {
    "united states of america": "US", "united states": "US", "usa": "US", "u.s.a.": "US",
    "u.s.": "US", "america": "US", "canada": "CA", "mexico": "MX", "méxico": "MX",
    "brazil": "BR", "brasil": "BR", "argentina": "AR", "colombia": "CO", "chile": "CL",
    "peru": "PE", "uruguay": "UY", "costa rica": "CR", "united kingdom": "GB", "uk": "GB",
    "u.k.": "GB", "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB", "ireland": "IE", "germany": "DE", "deutschland": "DE",
    "france": "FR", "spain": "ES", "portugal": "PT", "italy": "IT", "netherlands": "NL",
    "the netherlands": "NL", "belgium": "BE", "switzerland": "CH", "austria": "AT",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI", "poland": "PL",
    "czech republic": "CZ", "czechia": "CZ", "romania": "RO", "hungary": "HU",
    "greece": "GR", "ukraine": "UA", "serbia": "RS", "croatia": "HR", "bulgaria": "BG",
    "lithuania": "LT", "latvia": "LV", "estonia": "EE", "slovakia": "SK", "slovenia": "SI",
    "luxembourg": "LU", "turkey": "TR", "türkiye": "TR", "israel": "IL",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA", "qatar": "QA",
    "egypt": "EG", "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "morocco": "MA",
    "india": "IN", "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK",
    "singapore": "SG", "malaysia": "MY", "indonesia": "ID", "philippines": "PH",
    "vietnam": "VN", "thailand": "TH", "japan": "JP", "south korea": "KR", "korea": "KR",
    "china": "CN", "hong kong": "HK", "taiwan": "TW", "australia": "AU",
    "new zealand": "NZ",
}

FOREIGN_CITIES = {
    "london": "GB", "manchester": "GB", "edinburgh": "GB", "dublin": "IE", "berlin": "DE",
    "munich": "DE", "münchen": "DE", "hamburg": "DE", "frankfurt": "DE", "stuttgart": "DE",
    "amsterdam": "NL", "paris": "FR", "madrid": "ES", "barcelona": "ES", "lisbon": "PT",
    "milan": "IT", "zurich": "CH", "zürich": "CH", "geneva": "CH", "vienna": "AT",
    "stockholm": "SE", "oslo": "NO", "copenhagen": "DK", "helsinki": "FI", "warsaw": "PL",
    "krakow": "PL", "kraków": "PL", "wroclaw": "PL", "prague": "CZ", "bucharest": "RO",
    "budapest": "HU", "athens": "GR", "kyiv": "UA", "belgrade": "RS", "tel aviv": "IL",
    "dubai": "AE", "bangalore": "IN", "bengaluru": "IN", "hyderabad": "IN", "pune": "IN",
    "mumbai": "IN", "chennai": "IN", "gurgaon": "IN", "gurugram": "IN", "noida": "IN",
    "new delhi": "IN", "delhi": "IN", "singapore": "SG", "tokyo": "JP", "seoul": "KR",
    "shanghai": "CN", "beijing": "CN", "shenzhen": "CN", "sydney": "AU", "melbourne": "AU",
    "brisbane": "AU", "auckland": "NZ", "são paulo": "BR", "sao paulo": "BR",
    "mexico city": "MX", "ciudad de méxico": "MX", "guadalajara": "MX", "bogotá": "CO",
    "bogota": "CO", "buenos aires": "AR", "santiago": "CL", "manila": "PH",
    "kuala lumpur": "MY", "jakarta": "ID", "cairo": "EG", "cape town": "ZA", "lagos": "NG",
    "nairobi": "KE",
}

# fmt: on
US_CODES = frozenset(US_STATES.values())
CA_CODES = frozenset(CA_PROVINCES.values())

SUPRA_NA = re.compile(
    r"\b(north america|n\. america|americas|us\s*(?:/|&|and|or)\s*canada"
    r"|canada\s*(?:/|&|and|or)\s*us)\b",
    re.IGNORECASE,
)
SUPRA_OTHER = re.compile(
    r"\b(emea|europe|european union|eu|apac|asia|asia[- ]pacific|latam|latin america"
    r"|south america|central america|middle east|africa|oceania|anz|dach|nordics|benelux)\b",
    re.IGNORECASE,
)
REMOTE = re.compile(
    r"\b(remote|anywhere|work from home|wfh|distributed|telecommute|home[- ]based|virtual)\b",
    re.IGNORECASE,
)
HYBRID = re.compile(r"\bhybrid\b", re.IGNORECASE)
_CODE_AFTER_COMMA = re.compile(r",\s*([A-Z]{2})\b")
_US_TOKEN = re.compile(r"(?<![A-Za-z])(US|USA|U\.S\.|U\.S\.A\.)(?![A-Za-z])")
_CA_TOKEN = re.compile(r"(?<![A-Za-z])(CAN)(?![A-Za-z])")
_NOT_A_COUNTRY = re.compile(
    r"\b(latin|south|central|north)\s+america\b|\bnew\s+mexico\b", re.IGNORECASE
)
_DC = re.compile(r"\bD\.?C\.?(?![A-Za-z])")
_ONTARIO = re.compile(r"\bON\b|ontario")
_WASHINGTON_STATE = re.compile(r"\bWA\b|washington", re.IGNORECASE)
_TBILISI = re.compile(r"tbilisi", re.IGNORECASE)


def _phrases(words: Mapping[str, object]) -> re.Pattern[str]:
    alternatives = sorted(words, key=len, reverse=True)  # "new mexico" before "mexico"
    return re.compile(
        r"(?<![\w])(" + "|".join(map(re.escape, alternatives)) + r")(?![\w])",
        re.IGNORECASE,
    )


_STATE_RE = _phrases(US_STATES)
_PROVINCE_RE = _phrases(CA_PROVINCES)
_CITY_RE = _phrases(CITIES)
_COUNTRY_RE = _phrases(COUNTRIES)
_FOREIGN_CITY_RE = _phrases(FOREIGN_CITIES)


@lru_cache(maxsize=65536)
def parse_location(text: str) -> tuple[frozenset[str], frozenset[str], bool, bool]:
    """(countries, regions, remote, non-North-American region) for one location string."""
    countries: set[str] = set()
    regions: set[str] = set()
    t = text.strip()
    if not t:
        return frozenset(), frozenset(), False, False
    remote = bool(REMOTE.search(t))
    if SUPRA_NA.search(t):
        countries |= {"US", "CA"}
    other_region = bool(SUPRA_OTHER.search(t))
    # Foreign countries and cities first, so ", IN" after "Bengaluru" or ", IL" after
    # "Tel Aviv" isn't read as Indiana or Illinois below.
    for m in _COUNTRY_RE.finditer(_NOT_A_COUNTRY.sub(" ", t)):
        countries.add(COUNTRIES[m.group(1).lower()])
    for m in _FOREIGN_CITY_RE.finditer(t):
        name = m.group(1).lower()
        if not (name == "london" and _ONTARIO.search(t)):
            countries.add(FOREIGN_CITIES[name])
    foreign = countries - {"US", "CA"}
    # ", XX" codes: ", CA" is California unless a Canadian province code precedes it.
    codes = _CODE_AFTER_COMMA.findall(t)
    for i, code in enumerate(codes):
        if code in foreign:
            continue
        if code in CA_CODES:
            countries.add("CA")
            regions.add(f"CA-{code}")
        elif code == "CA" and i > 0 and codes[i - 1] in CA_CODES:
            countries.add("CA")
        elif code in US_CODES:
            countries.add("US")
            regions.add(f"US-{code}")
        elif code == "US":
            countries.add("US")
    for m in _PROVINCE_RE.finditer(t):
        countries.add("CA")
        regions.add(f"CA-{CA_PROVINCES[m.group(1).lower()]}")
    for m in _STATE_RE.finditer(t):
        name = m.group(1).lower()
        if name == "georgia" and _TBILISI.search(t):
            continue
        if name == "washington" and _DC.search(t):
            name = "district of columbia"
        countries.add("US")
        regions.add(f"US-{US_STATES[name]}")
    for m in _CITY_RE.finditer(t):
        country, region = CITIES[m.group(1).lower()]
        if m.group(1).lower() == "vancouver" and _WASHINGTON_STATE.search(t):
            country, region = "US", "WA"
        countries.add(country)
        if region:
            regions.add(f"{country}-{region}")
    if _US_TOKEN.search(t):
        countries.add("US")
    if _CA_TOKEN.search(t):
        countries.add("CA")
    return frozenset(countries), frozenset(regions), remote, other_region


def classify(
    locations: list[str],
    country_hints: list[str] | None = None,
    workplace_type: str | None = None,
) -> dict[str, list[str] | str]:
    """Countries, regions and bucket from all of a job's locations and country hints."""
    countries: set[str] = set()
    regions: set[str] = set()
    remote = workplace_type == "remote"
    other_region = False
    for hint in country_hints or []:
        hint = hint.strip()
        if len(hint) == 2 and hint.isalpha():  # an ISO code (Lever, SmartRecruiters)
            countries.add(hint.upper())
        elif hint:
            c, r, _, o = parse_location(hint)
            countries |= c
            regions |= r
            other_region |= o
    for location in locations:
        c, r, rem, o = parse_location(location)
        countries |= c
        regions |= r
        remote |= rem
        other_region |= o
    if countries & {"US", "CA"}:
        bucket = "us_ca"
    elif countries or other_region:
        bucket = "other"
    elif remote:
        bucket = "remote_unspecified"
    else:
        bucket = "unknown"
    return {
        "countries": sorted(countries),
        "regions": sorted(regions),
        "geo_bucket": bucket,
    }


def workplace_from_text(*texts: str | None) -> str | None:
    """`hybrid` or `remote` when a free-text location says so, else None."""
    joined = " ".join(t for t in texts if t)
    if HYBRID.search(joined):
        return "hybrid"
    if REMOTE.search(joined):
        return "remote"
    return None

"""Canonical RMP-name-variant -> trace-name alias map (normalized keys).

Single source of truth shared by precompute.py (build time) and server.py
(runtime). These were previously two hand-synced copies that had drifted:
the server copy was missing 62 of the aliases the catalog was built with.
"""

ALIAS_MAP = {
    "laney strange": "elena strange",
    "ben tasker": "benjamin tasker",
    "alberto de la torre": "alberto de la torre duran",
    "justin wang": "hsiao-an wang",
    "sakib miazi": "md nazmus sakib miazi",
    "nazmus miazi": "md nazmus sakib miazi",
    "alex depaoli": "alexander depaoli",
    "denisee spencer": "denise spencer",
    "chris bruell": "christopher bruell",
    "hande ondemir": "hande musdal ondemir",
    "francis frank georges": "francis georges",
    "isabel campos": "isabel sobral campos",
    "mary sue potts-santone": "mary-susan potts-santone",
    "ronald c. zullo": "ronald zullo",
    "steve granelli": "steven granelli",
    "william (bill) goldman": "william goldman",
    "virgiliu pavlu": "virgil pavlu",
    "zhiyuan (katherine) zhang": "zhiyuan zhang",
    "katherine zhang": "zhiyuan zhang",
    "bill goldman": "william goldman",
    "aarti sathyanaran": "aarti sathyanarayana",
    "akash murty": "akash murthy",
    "ali chaleshtari": "ali shirzadeh chaleshtari",
    "sriram rajagopalan": "sriramasundarar rajagopalan",
    "mauricio codesso": "mauricio mello codesso",
    "magda cooney": "magdalena cooney",
    "john lowery": "john lowrey",
    "iesha karasik": "ieshia karasik",
    "ifa khan": "iffat khan",
    "h. david sherman": "h sherman",
    "ganish krisnamoorthy": "ganesh krishnamoorthy",
    "farena sultan": "fareena sultan",
    "cathy merlo": "catherine merlo",
    "ye yin": "yi yin",
    "silvio amir": "silvio amir alves moreira",
    "olin shivers": "olin shivers iii",
    "rush sanghrajka": "rushit sanghrajka",
    "john alexis gomez": "john alexis guerra gomez",
    "ghita amor tijani": "ghita amor-tijani",
    "bob lupi": "robert lupi",
    "hany sadaka": "hanai sadaka",
    "mary- susan potts": "mary-susan potts-santone",
    "xiaotao (kelvin) liu": "xiaotao liu",
    "kelvin liu": "xiaotao liu",
    "Alex Budnitz": "Alexander Budnitz",
    "Alex Martsinkovsky": "Alexander Martsinkovsky",
    "Anu Gaur": "Anupama Gaur",
    "Balasubrama Maheswaran": "Balasubramaniam Maheswaran",
    "Barb Murrer": "Barbara Murrer",
    "Ben Knudsen": "Benjamin Knudsen",
    "Ben Machlin": "Benjamin Machlin",
    "Bolo Amgalan": "Bolor Amgalan",
    "Brad Lehman": "Bradley Lehman",
    "Chris Ayala": "Christopher Ayala",
    "Chris Beasley": "Christopher Beasley",
    "Chris Bosso": "Christopher Bosso",
    "Chris King": "Christopher King",
    "Chris Robertson": "Christopher Robertson",
    "Chris Selland": "Christopher Selland",
    "Christo Wilson": "Christopher Wilson",
    "Dan Kennedy": "Daniel Kennedy",
    "Dan Lothian": "Daniel Lothian",
    "Dan Matthew": "Daniel Matthew",
    "Dan Metzger": "Daniel Metzger",
    "Dan Sunderland": "Daniel Sunderland",
    "Dan Urman": "Daniel Urman",
    "Dan Zedek": "Daniel Zedek",
    "Ed Witten": "Edward Witten",
    "Gahye Song": "Ga Hye Song",
    "Ganeshsingh Thakur": "Ganesh Thakur",
    "Greg Allen": "Gregory Allen",
    "Greg Collier": "Gregory Collier",
    "Greg Fiete": "Gregory Fiete",
    "Greg Goodale": "Gregory Goodale",
    "Greg Kowalski": "Gregory Kowalski",
    "Greg Wassall": "Gregory Wassall",
    "Jean Francois Hamel": "Jean-Francois Hamel",
    "Jeff Howe": "Jeffrey Howe",
    "Jeff Kushner": "Jeffrey Kushner",
    "Ji-Yong Shin": "Ji Yong Shin",
    "Kat Gonso": "Kathleen Gonso",
    "Ken Baclawski": "Kenneth Baclawski",
    "Kris Dorsey": "Kristen Dorsey",
    "Marie-Odile Hobeika": "Marie Odile Hobeika",
    "Matt Garcia": "Matthew Garcia",
    "Matt Hunt": "Matthew Hunt",
    "Matt Lee": "Matthew Lee",
    "Matt Williams": "Matthew Williams",
    "Meg Heckman": "Meghan Heckman",
    "Mitch Franklin": "Mitchell Franklin",
    "Muhammad Shabanpour": "Muhammadhussian Shabanpour",
    "Nadaa Naji": "Nada Naji",
    "N. Castor": "Nicole Castor",
    "Pierre Tchetgen": "Pierre-Valery Tchetgen",
    "Ray Weaver": "Raymond Weaver",
    "seo eun (sunny) yang": "seoeun yang",
    "yang seoeun": "seoeun yang",
    "Sheng Yen": "Sheng-Che Yen",
    "Tim Brown": "Timothy Brown",
    "Tim Rupert": "Timothy Rupert",
    "A Zilleruelo": "Arturo Zilleruelo",
    # TRACE spells both O'Malleys with an apostrophe; RMP has three variants.
    "don o'malley": "donald o'malley",
    "donald o' malley": "donald o'malley",
    "donica omalley": "donica o'malley",
    "G. Kimball": "Grayson Kimball",
    "R Cole Eidson": "Robert Eidson",
    "S. M Gupta": "Surendra Gupta",
    "sarthak gupta": "sarthak suhrid gupta",
    # Verified by hand: teaches analytics at the Roux Institute / CPS (BUSN) and
    # information systems in Engineering (INFO), so the automatic
    # course-subject check reads the two halves as different people.
    # https://cps.northeastern.edu/faculty/dan-koloski/
    # https://coe.northeastern.edu/people/koloski-daniel/
    "dan koloski": "daniel koloski",
    # ── Two different men in one department, both going by "Peter Xu". RMP knows
    # them only as "Peter", TRACE only by their legal first names, so neither
    # linked up and no automatic rule can separate them: same surname, same
    # department, both teaching a 2301-level supply chain course. Resolved by
    # matching each RMP listing's course code to its TRACE sections.
    #
    #   RMP "Peter Xu" (id 2875022, 11 reviews, MGSC2301, 2023-11..2026-03)
    #     -> TRACE "peng xu" (15 sections incl. MGSC2301, Fall 2022..Fall 2025)
    #     https://damore-mckim.northeastern.edu/people/pengpeter-xu/  ("Peng(Peter) Xu")
    #
    #   RMP "Peter (Xun) Xu" (id 3161329, 4 reviews, "2301"/supply chain, 2026-04..05)
    #     -> TRACE "xun xu" (SCHM2301 + MISM6401/6405, Spring 2025 onward)
    #     https://damore-mckim.northeastern.edu/people/xun-xu/
    #
    # Do not collapse these two into each other: the review dates alone rule it
    # out (Peng's reviews start in 2023, Xun has no sections before Spring 2025).
    "peter xu": "peng xu",
    "peter (xun) xu": "xun xu",
    # ── Promoted from the fuzzy matcher (see fuzzy_trace_match in precompute.py).
    # Recording them here makes the RMP->TRACE link exact and deterministic, and
    # collapses the duplicate catalog row each fuzzy match used to leave behind.
    # Confirmed by shared course subject between RMP reviews and TRACE sections (54):
    "abdul shariq mohammed": "abdul raheem shariq mohammed",
    "anna thimsen": "anna freya thimsen",
    "ant woodall": "anthony woodall",
    "armand gatien wetie": "armand gatien ngounou wetie",
    "asimina nikolopoulou": "asimina ino nikolopoulou",
    "ayse yildirim": "ayse bilge yildirim",
    "ben boossarangsi": "benjamin boossarangsi",
    "ben wormwood": "benjamin wormwood",
    "bob schutter": "bob de schutter",
    "brad hatfield": "bradley hatfield",
    "brooke foucault welles": "brooke welles",
    "cali collin": "cali-ryan collin",
    "carlos dominguez": "carlos casso dominguez",
    "catalina almanza": "catalina herrera almanza",
    "celine esch": "celine de esch",
    "christopher grimley": "chris grimley",
    "constantin takacs": "constantin nicolae takacs",
    "courtney minard": "courtney sara minard",
    "dan grindle": "daniel grindle",
    "dan quinn": "daniel quinn",
    "daniel voionmaa": "daniel noemi voionmaa",
    "don goldthwaite": "donald goldthwaite",
    "don king": "donald king",
    "geoff davies": "geoffrey davies",
    "heidi feldman": "heidi kevoe feldman",
    "helen markewich": "helen ann markewich",
    "hongli (julie) zhu": "hongli zhu",
    "ioana bogdan": "ioana corina bogdan",
    "j. timothy sage": "j timothy sage",
    "jesica speed wiley": "jesica wiley",
    "jordan kemper": "jordan fox kemper",
    "jose martinez-lorenzo": "jose angel martinez-lorenzo",
    "kristen gonzalez": "kristen mathieu gonzalez",
    "lloyd tanlu": "lloyd john tanlu",
    "luigia (gina) maiellaro": "luigia maiellaro",
    "maria villar": "maria elena villar",
    "mary kate dodgson": "mary dodgson",
    "mohammad dehghani": "mohammad mohammad dehghani dehghani",
    "mohammad tavana": "mohammad khavari tavana",
    "monica borgida": "monica baraldi borgida",
    "naveen sapavath": "naveen naik sapavath",
    "or aharon": "or beit aharon",
    "pablo alvarez": "pablo boixeda alvarez",
    "pedro cruz": "pedro miguel cruz",
    "pooja balani": "pooja rajendra balani",
    "rafael tena": "rafael ubal tena",
    "ron thomas": "ronald thomas",
    "ruixiang ray wang": "ruixiang wang",
    "salma idrissi": "salma el idrissi",
    "samuel john gatley": "samuel gatley",
    "stephani laverdiere": "stephanie laverdiere",
    "theodore (ted) landsmark": "theodore landsmark",
    "xuezhu jenny lu": "xuezhu lu",
    "ziyi zoey zhao": "ziyi zhao",
    # Name-pattern only — nickname/middle-name on a rare surname (19).
    # Spot-checked against Northeastern directories; no course overlap available.
    "alina lungeanu": "alina ionica lungeanu",
    "caitlin rapoport": "caitlin smith rapoport",
    "chai mutsalklisana": "chaiyaporn mutsalklisana",
    "chris doty": "christopher doty",
    "ga song": "ga hye song",
    "greg aloupis": "gregory aloupis",
    "guilherme vieira": "guilherme salvador vieira",
    "jeff galkowski": "jeffrey galkowski",
    "leila someh": "leila keyvani someh",
    "magy el-nasr": "magy seif el-nasr",
    "mariana mestre": "mariana valencia mestre",
    "matt rubins": "matthew rubins",
    "najla mouchrek": "najla miranda mouchrek",
    "noor ali": "noor ul sabah ali",
    "shaowei huang": "shao-wei huang",
    "steve mosenson": "steven mosenson",
    "tim orwig": "timothy orwig",
    "valerio laredo": "valerio toledano laredo",
    "zorana isautier": "zorana matic isautier",
}

def _normalize_name(name: str) -> str:
    import re, unicodedata
    s = str(name).strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip()
    return s

# Ensure keys/values match normalize_name() used by server.py/precompute.py.
ALIAS_MAP = {_normalize_name(k): _normalize_name(v) for k, v in ALIAS_MAP.items()}


def _name_to_slug(name: str) -> str:
    """Mirror of name_to_slug() in precompute.py and server.py."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# Retired slug -> current slug.
#
# precompute builds the catalog primary key as name_to_slug(_name_key), and
# _name_key is the *aliased* name, so adding an entry above renames that
# professor's slug: aliasing "dan koloski" to "daniel koloski" turns
# /professors/dan-koloski into /professors/daniel-koloski. Bookmarks store the
# bare slug, links get shared, and the name_key fallback in the read path cannot
# recover it (it derives "dan koloski", but the stored name_key is now "daniel
# koloski"), so without this the old URL 404s and the bookmark silently vanishes.
#
# Resolution order matters: callers consult this only *after* a direct slug
# lookup misses, so a live professor whose real slug happens to equal a retired
# one always wins. Single-hop, matching the single-pass .replace(ALIAS_MAP) in
# precompute — an alias whose target is itself an alias key is not chased.
SLUG_ALIASES = {
    _name_to_slug(k): _name_to_slug(v)
    for k, v in ALIAS_MAP.items()
    if _name_to_slug(k) != _name_to_slug(v)
}


def canonical_slug(slug):
    """Current slug for a possibly-retired professor slug, or None if unknown."""
    if not slug:
        return None
    return SLUG_ALIASES.get(str(slug).strip().lower())


def _build_retired_slugs():
    out = {}
    for old, new in SLUG_ALIASES.items():
        out.setdefault(new, []).append(old)
    return out


# Current slug -> every retired slug now pointing at it. The inverse of
# SLUG_ALIASES, for delete paths that have to match whichever key was originally
# stored rather than the one being displayed today.
RETIRED_SLUGS = _build_retired_slugs()


def retired_slugs(slug):
    """Every retired slug that now resolves to `slug` (empty list if none)."""
    if not slug:
        return []
    return RETIRED_SLUGS.get(str(slug).strip().lower(), [])

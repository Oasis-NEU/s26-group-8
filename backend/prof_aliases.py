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
}


# Distinct people the surname fuzzy match in precompute.attach_fuzzy_trace
# would otherwise merge, because one first name is a prefix of the other the
# same way a nickname is: "yan" of "yaning", "michael" of "michaela". ALIAS_MAP
# cannot express this — it maps a name onto another name, and what is needed
# here is the refusal to.
#
# Nothing lexical separates these from "dan" -> "daniel", and department does
# not either: cross-college teaching is common, so a college mismatch flagged
# three legitimate matches (Lungeanu, Koloski, Laverdiere) for every real
# collision it caught. Entries are added by hand when someone spots one.
#
# michaela lewis is *also* caught by the trace_courses check in
# attach_fuzzy_trace, which needs no list; she is here so the pair is recorded
# in one place if her TRACE courses ever go away.
FUZZY_DENY = {
    ("yan li", "yaning li"),
    ("michaela lewis", "michael lewis"),
}


def _normalize_name(name: str) -> str:
    import re, unicodedata
    s = str(name).strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip()
    return s

# Ensure keys/values match normalize_name() used by server.py/precompute.py.
ALIAS_MAP = {_normalize_name(k): _normalize_name(v) for k, v in ALIAS_MAP.items()}
FUZZY_DENY = {(_normalize_name(a), _normalize_name(b)) for a, b in FUZZY_DENY}

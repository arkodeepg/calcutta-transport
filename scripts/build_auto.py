"""Build data/auto/{routes,route_stops,stops}.csv.

Inputs: hand-entered community routes and March 2026 fare news (in this file), the official RTA Kolkata list
(sources/raw/auto_fares/rta_kolkata_5673wt_routes_transcribed.psv, 489 routes, notification 5673-WT of 10 Dec 2018),
2025-26 press items, and an Overpass dump of named OSM features (sources/raw/auto_fares/osm_nf_*.json).
Stop coordinates come only from OpenStreetMap-derived, ODbL data: (1) the earlier Nominatim answers kept in
sources/cache/nominatim_cache.json, (2) exact or locality name matches in the Overpass dump (name, name:en, alt_name,
old_name, official_name, short_name), (3) Nominatim with strict normalised name equality, (4) Photon as a last fallback.
Plausibility: stops outside the route's expected area, or implausibly far from the other stops, are left blank.
Nominatim is called at most once per second; the User-Agent carries no personal data.

Usage: venv/bin/python scripts/build_auto.py            (AUTO_NO_REMOTE=1 for a cache-only dry run)
Fetch the Overpass dump first with scripts/overpass_fetch.py (see sources/SOURCES_auto_fares.md, AF-OSMNF).
"""
import csv, re, json, time, difflib, math, urllib.request, urllib.parse, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root, resolved from this file
UA='calcutta-transport-dataset/0.2 (open transit dataset; +https://github.com/arkodeepg/calcutta-transport)'
def slug(s): return re.sub(r'[^a-z0-9]+','_',s.lower()).strip('_')
CI='AF-CI'; F3='AF-F3'
# (route_id, name, origin, dest, source, conf, notes, fare_min, fare_max, stops)
R=[]
def add(rid,o,d,src,conf,notes,stops,fmin='',fmax='',via=''):
    R.append(dict(rid=rid,o=o,d=d,src=src,conf=conf,notes=notes,stops=stops,fmin=fmin,fmax=fmax))
add('auto_r91','Phoolbagan','Karunamoyee',CI+'|'+F3,'community','Route no 91 (kolkatacityinfo, ~2017); 4.6 km via Narkeldanga Main Rd, Salt Lake Broad Way',
 'Phoolbagan|Suktara Cinema|Kadapara|Subhas Sarovar|Orient|89 Cinema|Swabhumi|Bypass|Salt Lake Stadium|Purbachal|13 No. Tank|Bijan Bhawan|HB Island|GD Island|FD Block|Netaji Park|Central Park|Karunamoyee'.split('|'))
add('auto_r58','Phoolbagan','Beliaghata',CI+'|'+F3,'community','Route no 58; 2.9 km via CIT Rd, Beliaghata Main Rd; CI lists CIT Building More as last stop, F3 lists Beliaghata',
 'Phoolbagan|Kali Mandir|Allahabad Bank|Trikon Park|Devine Nursing Home|Ratna Kebin|CIT More|ID Hospital|Jora Mandir|Ragini Cinema|CIT Building More'.split('|'))
add('auto_r17','Phoolbagan','Sealdah',CI,'community','Route no 17; 2.5 km via Narkeldanga Main Rd, N Sealdah Rd, Kaiser St, Canal West Rd',
 'Phoolbagan|Sishu Hospital|Chanditala|Rail Quarter|Sasthitala|Narkeldanga Post Office|Rajabazar Khaal Pool|Tasbir Mahal Cinema|Sealdah Station'.split('|'))
add('auto_r59','Phoolbagan','Ganesh Talkies',CI,'community','Route no 59; via CIT Rd Scheme VI-M, Manicktala Main Rd, Vivekananda Rd, Chittaranjan Ave. Source stop list ends at Girish Park, destination Ganesh Talkies appended as final stop',
 'Phoolbagan|Pantaloons|VIP Market|Kathgola|Kankurgachi|Rail Bridge|Marwari Bagan|Bagmari Bazar|Manicktala Khaal Pool|Manicktala Post Office|Blood Bank|Manicktala More|Hedua|Girish Park|Ganesh Talkies'.split('|'))
add('auto_r80','ID Hospital','RG Kar Hospital',CI+'|'+F3,'community','Route no 80; 5.6 km via CIT Rd, Manicktala Main Rd, Raja Dinendra St, RG Kar Rd. Overlaps r58 and r59 stop sequences',
 'ID Hospital|CIT More|Ratna Kebin|Devine Nursing Home|Trikon Park|Allahabad Bank|Kali Mandir|Phoolbagan|Pantaloons|VIP Market|Kathgola|Kankurgachi|Rail Bridge|Marwari Bagan|Bagmari Bazar|Manicktala Khaal Pool|Manicktala Post Office|KMC Ward Office|Paresnath Temple|Gauri Bari|Ayurvedic Hospital|Deshbandhu Park|Park Institution|RG Kar Hospital'.split('|'))
add('auto_r44','Kadapara','Machua',CI+'|'+F3,'community','Route no 44; via Narkeldanga Main Rd, Keshab Chandra Sen St, Madan Mohan Burman St. The community stop list (AF-CI, same in AF-F3) does not fit the termini: it starts at Suktara Cinema before Kadapara and runs past Machua to MG Road Metro, so treat the first and last stops as unverified',
 'Suktara Cinema|Kadapara|Phoolbagan|Sishu Hospital|Chanditala|Rail Quarter|Sasthitala|Narkeldanga Post Office|Rajabazar Khaal Pool|Tasbir Mahal Cinema|Raja Bazar|Amherst Street Crossing|College Street Bata|MG Road Metro'.split('|'))
add('auto_r73','Beliaghata','Sealdah',CI,'community','Route no 73; via Beliaghata Main Rd',
 'Beliaghata|Ragini Cinema|ID Hospital|CIT More|Rashmani Bazar|Deshbandhu School|Alochaya|Sarkar Bazar|Borof Kol|Khal Pool|Sales Tax|Sealdah Station'.split('|'))
add('auto_r52','Ultadanga','Baguiati',CI,'community','Route no 52; via VIP Rd',
 'Ultadanga|Kolaghata|Sreebhumi|Lake Town|Bangur|Dum Dum Park|Keshtopur|Narayantala|Baguiati'.split('|'))
add('auto_r8','Howrah','Botanical Garden',CI,'unverified','Route no 8; source gives only terminals (Howrah, Botanical Garden), no distance or stops; probably Howrah Station',
 ['Howrah Station','Botanical Garden'])
add('auto_r94','Kankurgachi','Karunamoyee',CI+'|'+F3,'community','Route no 94; via Manicktala Main Rd, EM Bypass, Salt Lake Rd. Kankurgachi to Karunamoyee fare rose by Rs 3 around 24 Mar 2026 (Bartaman), absolute fare not stated',
 ['Kankurgachi','Karunamoyee'])
add('auto_r93','SAI Complex','Karunamoyee',CI,'community','Route no 93; via Salt Lake Broad Way, Beliaghata Bypass',
 'SAI Complex|Columbia Asia Hospital|EZCC|Bijan Bhawan|HB Island|GD Island|FD Block|Netaji Park|Central Park|Karunamoyee'.split('|'))
add('auto_r57','Salt Lake','Ultadanga',CI,'unverified','Route no 57; source gives only terminals and no stops or distance; Salt Lake end not specified',
 ['Ultadanga'])
add('auto_r14','MG Road','BK Pal',CI,'community','Route no 14; via Rabindra Sarani Chitpur',
 'MG Road Metro|Ganesh Talkies|Ahiritola|BK Pal'.split('|'))
add('auto_ballygunge_judges_court','Ballygunge Station','Judges Court',F3,'community','F3Kolkata 2017; stop order as listed by source, not verified',
 'Ballygunge Station|Gariahat|Triangular Park|Deshopriyo Park|Rashbehari|Kalighat Metro|SP Mukherjee Road|Hazra|Kalighat'.split('|'))
add('auto_tollygunge_phari_behala_td','Tollygunge Phari','Behala Tram Depot',F3,'community','F3Kolkata 2017',
 'Tollygunge Phari|Mahabir Tala|New Alipore|Taratala|Ajanta Cinema|Behala Thana|Behala Tram Depot'.split('|'))
add('auto_gariahat_behala_td','Gariahat','Behala Tram Depot',F3,'community','F3Kolkata 2017',
 'Gariahat|Triangular Park|Deshopriyo Park|Rashbehari|Kalighat Metro|Chetla|New Alipore|Taratala|Ajanta Cinema|Behala Thana|Behala Tram Depot'.split('|'))
add('auto_behala_td_ballygunge_stn','Behala Tram Depot','Ballygunge Station',F3,'community','F3Kolkata 2017',
 'Behala Tram Depot|Behala Thana|Ajanta Cinema|Taratala Crossing|New Alipore|Chetla|Rashbehari|Deshopriyo Park|Gariahat|Ballygunge Station'.split('|'))
add('auto_chetla_park_behala_chowrasta','Chetla Park','Behala Chowrasta',CI+'|'+F3,'unverified','Only the title is available (F3 article not retrievable); no stops, fare or distance',
 ['Chetla Park','Behala Chowrasta'])
NEWS=[ # fare routes from Mar 2026 news
 ('auto_ultadanga_sector_v','Ultadanga','Sector V (Salt Lake)',35,40,'AF-BART|AF-AJT','Fare Rs 40 from 23 Mar 2026 (was 35)'),
 ('auto_ultadanga_bikash_bhavan','Ultadanga','Bikash Bhavan (Salt Lake)',20,25,'AF-BART','Fare Rs 25 from 23 Mar 2026 (was 20)'),
 ('auto_ultadanga_karunamoyee','Ultadanga','Karunamoyee (Salt Lake)',20,25,'AF-BART','Fare Rs 25 from 23 Mar 2026 (was 20)'),
 ('auto_ultadanga_tank_12','Ultadanga','Tank No 12 (Salt Lake)',25,30,'AF-AJT|AF-EIM','Fare Rs 30 from 23 Mar 2026 (was 25)'),
 ('auto_tank_13_phoolbagan','Tank No 13 (Salt Lake)','Phoolbagan',14,16,'AF-AJT|AF-EIM','Fare Rs 16 from 23 Mar 2026 (was 14); likely part of route 91 corridor'),
 ('auto_maniktala_phoolbagan','Maniktala','Phoolbagan',15,17,'AF-AJT|AF-EIM','Fare Rs 17 from 23 Mar 2026 (was 15)'),
 ('auto_phoolbagan_girish_park','Phoolbagan','Girish Park',20,23,'AF-AJT|AF-EIM','Fare Rs 23 from 23 Mar 2026 (was 20); likely part of route 59 corridor'),
 ('auto_behala_chowrasta_sarsuna','Behala Chowrasta','Sarsuna',18,25,'AF-BART','Fare Rs 25 from 23 Mar 2026 (was 18); night fares reportedly Re 1 higher'),
 ('auto_chingrighata_sdf','Chingrighata','SDF (Salt Lake)',15,18,'AF-BHUNT|AF-SUBK','Fare Rs 18 around 11 Mar 2026 (was 15); passengers alleged no official approval'),
 ('auto_garia_baruipur','Garia','Baruipur',None,None,'AF-BHUNT|AF-SUBK','Fare rose Rs 5 to 10 around 11 Mar 2026, absolute fare not given'),
 ('auto_sonarpur_garia','Sonarpur','Garia',None,None,'AF-BHUNT','Fare rose around 11 Mar 2026, absolute fare not given'),
 ('auto_baruipur_dakshin_barasat','Baruipur','Dakshin Barasat',None,None,'AF-SUBK','Fare rose around 11 Mar 2026, absolute fare not given'),
 ('auto_baruipur_julpia','Baruipur','Julpia',None,None,'AF-SUBK','Fare rose around 11 Mar 2026, absolute fare not given'),
]
for rid,o,d,f0,f1,src,n in NEWS:
    add(rid,o,d,src,'community','News only, terminals only. '+n,[o,d],f1 if f1 else '',f1 if f1 else '')

# ======================================================================
# Official RTA Kolkata list (Notification 5673-WT, 10 Dec 2018, 489 routes)
# transcribed by hand from the 11 scanned page images (sources/raw/auto_fares/wt5673/).
# Only origin, destination and via text are kept; maximum permit strength is not used
# because the digits are not reliably legible in the scans.
ORIG_RIDS={r['rid'] for r in R}   # the first 31 hand-built routes: only these may use the earlier loose Nominatim matcher
OFF_SRC='AF-RTA5673'
OFF_FILE=os.path.join(ROOT,'sources','raw','auto_fares','rta_kolkata_5673wt_routes_transcribed.psv')
# existing hand-built routes that are the same corridor as an official serial (exact terminals)
OFFICIAL_MATCH={'auto_r91':[254],'auto_r58':[216],'auto_r59':[217],'auto_r80':[238],'auto_r44':[202],
 'auto_r52':[242],'auto_r14':[173],'auto_r94':[252],'auto_ballygunge_judges_court':[163],
 'auto_tollygunge_phari_behala_td':[233],'auto_chetla_park_behala_chowrasta':[260],
 'auto_ultadanga_sector_v':[100,122],'auto_chingrighata_sdf':[113],'auto_behala_chowrasta_sarsuna':[45],
 'auto_tank_13_phoolbagan':[213],'auto_ultadanga_bikash_bhavan':[149],'auto_ultadanga_karunamoyee':[89,243]}
MATCHED={s for v in OFFICIAL_MATCH.values() for s in v}
# serials whose origin/destination text needs a manual split (list of stop names in order, or list of variants)
SPECIAL={
 53:[['Behala Police Station','Parnasree'],['Behala Police Station','Rabindranagar'],['Behala Police Station','Zinzira Bazar']],
 54:[['Behala Tram Depot','Chakkendua'],['Behala Tram Depot','Joy Jala'],['Behala Tram Depot','Sen Pally']],
 183:[['Dum Dum Station','Gun Shell Factory','Cossipur Ferry Ghat']],
 184:[['Deshapriya Park','Lords Bakery']],
 188:[['Gariahat','New Alipore E Block']],
 192:[['HUDCO','Karunamoyee','SDF Building']],
 201:[['Kasba','Ballygunge Phari']],
 204:[['Kustia','Ekdalia Road near Bharat Sevashram']],
 10:[['Bhattanagar Daspara','Liluah Station']],
 226:[['Rashbehari Avenue Pratapaditya Road Crossing','Chetla Dalminya Park']],
 262:[['Dhapa','Chittaranjan Hospital']],
 326:[['Barrackpore Station','Nilganj']],
 94:[['New Town Eco Park 1 No Gate','Jagatpur']],
 374:[['Durganagar Station','Majerhat (serial 374, place not identified)']],
}
# spelling and station normalisation so the same place gets one stop id across routes
ABBR=[(r'\bStn\.?(?=\s|$)','Station'),(r'\bRly\.?(?=\s|$)','Railway'),(r'\bP\.?\s?S\.?(?=\s|$)','Police Station'),
 (r'\bT\.?\s?D\.?(?=\s|$)','Tram Depot'),(r'\bTramdepo\b','Tram Depot'),(r'\bB\.?T\.? College\b','B T College'),
 (r'\bNo\.\s*','No '),(r'\s+',' ')]
ALIAS={k.lower():v for k,v in {
 'Karunamayee':'Karunamoyee','Karunamoyee Housing 1 No Gate':'Karunamoyee Housing 1 No Gate','Phool Bagan':'Phoolbagan','Phool Bagan More':'Phoolbagan More',
 'Ultadanga Station':'Ultadanga','Ultadanga Stn':'Ultadanga','Ultadnga':'Ultadanga','Baguihati':'Baguiati','Baguiati':'Baguiati',
 'Majherhat':'Majerhat','Majerhat Station':'Majerhat','Salt Lake Sector 5':'Sector V','Sector 5':'Sector V','Salt Lake 13 No Tank':'13 No. Tank','13 No Tank':'13 No. Tank',
 'Salt Lake 10 No Tank':'10 No Tank','Salt Lake 4 No Tank':'4 No Tank','Salt Lake':'Salt Lake','Hudco':'HUDCO','Chiriamore':'Chiria More',
 'Barrackpore Chiriamore':'Barrackpore Chiria More','Barrackpore Railway Station':'Barrackpore Station','Barrackpore Rly Station':'Barrackpore Station',
 'Airport 1 No':'1 No Airport Gate','Behala Tram Depot':'Behala Tram Depot','Behala T.D.':'Behala Tram Depot','Behala Tramdepo':'Behala Tram Depot',
 'Behala Chowrasta':'Behala Chowrasta','Behala Chowrasta Muchipara':'Behala Chowrasta','Tollygunge Tram Depot':'Tollygunge Tram Depot',
 'Tolly Metro':'Tollygunge Metro','Garia Stn':'Garia Station','Garia Rail Station':'Garia Station','Jadavpur Stn':'Jadavpur Station','Jadavpur Police Station':'Jadavpur Police Station',
 'Jadavpore Police Station':'Jadavpur Police Station','Ruby General Hospital':'Ruby General Hospital','Sodepur Railway':'Sodepur Station','Khardaha Station Road':'Khardaha Station',
 'Cossipur 4B Bus Stand':'Cossipore 4B Bus Stand','Dum Dum Cantonment':'Dum Dum Cantonment',
 'Sinthi More 4B Bus Stand':'Sinthi More','Sinthi More':'Sinthi More','Sinthee More':'Sinthi More','Bannerjee Para':'Banerjee Para','Rathtala':'Rathtala',
 'Taratola':'Taratala','Gariahat Bata':'Gariahat','Gariahat':'Gariahat','Alipore Judge Court':'Alipore Judge Court','Gol Park':'Golpark',
 'Beliaghata I D Hospital':'ID Hospital','Beliaghata ID Hospital':'ID Hospital','Beliaghata By Pass':'Beliaghata Bypass','Bikash Bhawan':'Bikash Bhavan','Bikas Bhaban 6 No':'Bikash Bhavan',
 'RG Kar Hospital':'RG Kar Hospital','R G Kar Hospital':'RG Kar Hospital','R G Kar':'RG Kar Hospital','R.G. Kar Hospital':'RG Kar Hospital','R.G. Kar':'RG Kar Hospital',
 'Sodepur Rly Station':'Sodepur Station','Sodepur Railway Station':'Sodepur Station','Agarpara Station':'Agarpara Station',
 'Dakshineswar':'Dakshineswar','Noapara Metro':'Noapara Metro','Noa Para':'Noapara','Noa-Para':'Noapara','Belgachia Metro':'Belgachia Metro','Belgharia Station':'Belgharia Station',
 'Birati Rly Station':'Birati Station','Birati Railway Station':'Birati Station','Birati Stn':'Birati Station',
}.items()}
def clean(n):
    n=n.strip().rstrip('.')
    for a,b in ABBR: n=re.sub(a,b,n)
    n=n.replace('B T College','B.T. College')
    return ALIAS.get(n.lower(),ALIAS.get(n.lower().rstrip('.'),n))
def load_official():
    out=[]
    with open(OFF_FILE,encoding='utf-8') as f:
        next(f)
        for line in f:
            p=line.rstrip('\n').split('|')
            out.append((int(p[0]),p[1].strip(),p[2].strip(),p[3].strip()))
    return out
OFFICIAL=load_official()
assert [o[0] for o in OFFICIAL]==list(range(1,490)),'official list must be serials 1..489'
for rid,sers in OFFICIAL_MATCH.items():
    r=[x for x in R if x['rid']==rid][0]
    r['src']+='|'+OFF_SRC; r['conf']='verified'
    r['notes']+='. Same corridor appears in RTA Kolkata notification 5673-WT (10 Dec 2018) as serial '+'/'.join(map(str,sers))+' (terminals match; stop list is from the community source)'
for ser,o,d,via in OFFICIAL:
    if ser in MATCHED: continue
    variants=SPECIAL.get(ser,[[o,d]])
    for vi,stops in enumerate(variants):
        suffix='' if len(variants)==1 else '_'+'abc'[vi]
        note='RTA Kolkata notification 5673-WT (10 Dec 2018) serial %d, official route text read from scanned table'%ser
        if via: note+='; via/notes: '+via
        if len(variants)>1: note+='; one of %d alternative destinations listed under this serial'%len(variants)
        if ser in (183,188,192,204,226,10): note+='; stop names simplified from the official text'
        add('auto_rta_%03d%s'%(ser,suffix),stops[0],stops[-1],OFF_SRC,'verified',note,list(stops))
# ---- later official or press updates (see sources/SOURCES_auto_fares.md)
def note_on(rid,extra,src):
    r=[x for x in R if x['rid']==rid][0]; r['notes']+='; '+extra; r['src']+='|'+src
note_on('auto_rta_374','June 2025 press (indiahood.in, 14 Jun 2025) lists Durganagar Station to Majerhat among six routes newly approved by RTA Kolkata with 34 permits','AF-INDIAHOOD')
note_on('auto_rta_407','June 2025 press (indiahood.in) lists a Goruhat to Gorabazar via Rathtala Cantonment route with 115 permits; the 2018 text reads Gontrahat to Gorabazar and may be the same route, not confirmed','AF-INDIAHOOD')
note_on('auto_rta_434','June 2025 press (indiahood.in) lists Garubhanga (Nowdapara) to Ghosal Bhavan with 15 permits among newly approved routes','AF-INDIAHOOD')
for rid,o,d,n in (('auto_news2025_dumdum_nagerbazar','Dum Dum Station','Nagerbazar','300 permits'),
                  ('auto_news2025_sodepur_madhyamgram','Sodepur Station','Madhyamgram Station','204 permits'),
                  ('auto_news2025_bt_road_sajirhat','B.T. Road','Sajirhat','130 permits; the 2018 list already has Sodepur Station and B.T. College to Sajirhat, so this may overlap')):
    add(rid,o,d,'AF-INDIAHOOD','community','Press report (indiahood.in, 14 Jun 2025) of six routes newly approved by RTA Kolkata; terminals only, '+n,[o,d])
add('auto_howrah_chawalpatty_belanagar','Chawal Patty','Belanagar Railway Station','AF-HOWRAH-NOTICE','verified','Howrah district notice for a new contract carriage (auto rickshaw) route, notice period 23 Nov 2020 to 22 Sep 2021, via Health Centre, Samabay Pally, Saheb Bagan; whether the route was finally authorised is not known',
    ['Chawal Patty','Belanagar Railway Station'])
for rid,o,d,v in (('auto_rta_n24p_madhyamgram_bada','Madhyamgram Chowmatha','Bada','via Kharibari, 96 vacant permits'),
                  ('auto_rta_n24p_kholapota_72','Kholapota','72 No Bus Stand','79 vacant permits'),
                  ('auto_rta_n24p_malancha_sarberia','Malancha','Sarberia','68 vacant permits'),
                  ('auto_rta_n24p_habra_nagarukhra','Habra','Nagarukhra','65 vacant permits'),
                  ('auto_rta_n24p_malancha_ghoshpur','Malancha','Ghoshpur','61 vacant permits')):
    add(rid,o,d,'AF-MPOST-N24P','community','RTA North 24 Parganas (Barasat, Bongaon, Basirhat police districts) vacancy notice reported by Millennium Post, 3 Oct 2026, applications 28 Sep to 27 Oct 2026; one of the five routes with most vacancies out of 61, '+v+'; terminals only. Outside the RTA Kolkata list',[o,d])
print('routes total',len(R),file=sys.stderr)

# ======================================================================
# stop naming
CANON={'Howrah Station':'Howrah Station','Tank No 12 (Salt Lake)':'Tank No 12 Salt Lake','Tank No 13 (Salt Lake)':'13 No. Tank','Sector V (Salt Lake)':'Sector V','Bikash Bhavan (Salt Lake)':'Bikash Bhavan','Karunamoyee (Salt Lake)':'Karunamoyee','SDF (Salt Lake)':'SDF Building Salt Lake','CIT Building More':'CIT Building More'}
def cn(n):
    if n in CANON: return CANON[n]
    return clean(n)
# query overrides (Nominatim/Photon text) for names whose plain form does not search well
Q={'Phoolbagan':'Phoolbagan','Karunamoyee':'Karunamoyee','Ballygunge Station':'Ballygunge railway station','Sealdah Station':'Sealdah railway station','Howrah Station':'Howrah railway station','Behala Tram Depot':'Behala Tram Depot','Gariahat':'Gariahat','Judges Court':'Judges Court Road','MG Road Metro':'M G Road metro station','Kalighat Metro':'Kalighat metro station','Girish Park':'Girish Park','RG Kar Hospital':'R G Kar Medical College','ID Hospital':'Infectious Diseases Hospital Beliaghata','Botanical Garden':'Acharya Jagadish Chandra Bose Indian Botanic Garden','Sector V':'Sector V Salt Lake','Bikash Bhavan':'Bikash Bhavan Salt Lake','Sarsuna':'Sarsuna','Chingrighata':'Chingrighata','SDF Building Salt Lake':'SDF Building Salt Lake','Behala Chowrasta':'Behala Chowrasta'}

OLD_NAMES={cn(s) for r in R if r['rid'] in ORIG_RIDS for s in r['stops']}
names=[]
for r in R:
    for s in r['stops']:
        c=cn(s)
        if c not in names: names.append(c)
print(len(names),'unique stops',file=sys.stderr)

# ======================================================================
# geocoding: (1) previous Nominatim logic via cache (keeps earlier results), (2) strict match on an
# Overpass index of named OSM features, (3) Nominatim variants, (4) Photon. All ODbL (OSM) data.
cache_f=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','sources','cache','nominatim_cache.json')
cache=json.load(open(cache_f)) if os.path.exists(cache_f) else {}
BB=(88.15,22.30,88.55,23.05) # left,bottom,right,top: greater Kolkata incl. Barrackpore, Barasat, Kalyani edge
def log(msg):
    print(msg,file=sys.stderr)
_last=[0.0]
NO_REMOTE=bool(os.environ.get('AUTO_NO_REMOTE'))  # dry run: cache and local index only
def throttle():
    d=time.time()-_last[0]
    if d<1.1: time.sleep(1.1-d)
    _last[0]=time.time()
def nom_raw(q,bounded=True):
    """Nominatim search, cached. Max 1 request per second. No personal data in the User-Agent."""
    key=q if bounded else 'U|'+q
    if key in cache: return cache[key]
    if NO_REMOTE: return []
    p=dict(q=q,format='jsonv2',limit=5,addressdetails=0,countrycodes='in',viewbox='%s,%s,%s,%s'%(BB[0],BB[3],BB[2],BB[1]))
    if bounded: p['bounded']=1
    req=urllib.request.Request('https://nominatim.openstreetmap.org/search?'+urllib.parse.urlencode(p),headers={'User-Agent':UA})
    throttle()
    try: res=json.load(urllib.request.urlopen(req,timeout=30))
    except Exception as e: res=None; log('ERR nominatim %s %s'%(q,e))
    if res is None: return []
    cache[key]=res; json.dump(cache,open(cache_f,'w'),ensure_ascii=False); return res
def nom(q):  # legacy form used for the first 117 stops (query + ', Kolkata')
    if q in cache: return cache[q]
    if NO_REMOTE: return []
    p=urllib.parse.urlencode(dict(q=q+', Kolkata',format='jsonv2',limit=5,viewbox='%s,%s,%s,%s'%(BB[0],BB[3],BB[2],BB[1]),bounded=1,addressdetails=0,countrycodes='in'))
    req=urllib.request.Request('https://nominatim.openstreetmap.org/search?'+p,headers={'User-Agent':UA})
    throttle()
    try: res=json.load(urllib.request.urlopen(req,timeout=30))
    except Exception as e: res=None; log('ERR %s %s'%(q,e))
    if res is None: return []
    cache[q]=res; json.dump(cache,open(cache_f,'w'),ensure_ascii=False); return res
PH_F=os.path.join(ROOT,'sources','raw','auto_fares','photon_cache.json')
ph_cache=json.load(open(PH_F)) if os.path.exists(PH_F) else {}
def photon(q):
    if q in ph_cache: return ph_cache[q]
    if NO_REMOTE: return []
    p=urllib.parse.urlencode(dict(q=q,limit=5,lat=22.62,lon=88.38,bbox='%s,%s,%s,%s'%(BB[0],BB[1],BB[2],BB[3]),lang='en'))
    req=urllib.request.Request('https://photon.komoot.io/api/?'+p,headers={'User-Agent':UA})
    throttle()
    try: res=json.load(urllib.request.urlopen(req,timeout=30)).get('features',[])
    except Exception as e: res=None; log('ERR photon %s %s'%(q,e))
    if res is None: return []
    ph_cache[q]=res; json.dump(ph_cache,open(PH_F,'w'),ensure_ascii=False); return res
def sim(a,b):
    a,b=a.lower(),b.lower(); return difflib.SequenceMatcher(None,a,b).ratio()
STOP={'station','more','crossing','road','rd','park','hospital','cinema'}
def best(name):  # legacy matcher (unchanged behaviour)
    q=Q.get(name,name); res=nom(q)
    toks=[t for t in re.findall(r'[a-z0-9]+',name.lower()) if t not in STOP and len(t)>2] or re.findall(r'[a-z0-9]+',name.lower())
    for r in res:
        first=r['display_name'].split(',')[0]
        full=r['display_name'].lower()
        okname = sim(name,first)>=0.6 or sim(q,first)>=0.6 or all(t in first.lower() for t in toks)
        if okname and 'kolkata' in full or okname and ('howrah' in full or 'north 24' in full or 'south 24' in full):
            return r
    return None

def hav(a,b):
    R_=6371;la1,lo1,la2,lo2=map(math.radians,[a[0],a[1],b[0],b[1]])
    h=math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R_*math.asin(math.sqrt(h))
def inbb(lat,lon): return BB[1]<=lat<=BB[3] and BB[0]<=lon<=BB[2]

# ---- local index of named OSM features (Overpass dumps)
# words that may be dropped for the fallback (core) match: they only say what kind of spot it is inside a named locality
GEN=re.compile(r'\b(station|stn|railway|rly|bus|stand|stop|more|crossing|junction|bazar|bazaar|market|chowrasta)\b')
def nkey(s):
    s=s.lower().replace('&',' and ')
    s=re.sub(r'[^a-z0-9 ]+',' ',s); s=re.sub(r'\s+',' ',s).strip()
    s=s.replace('phool bagan','phoolbagan').replace('chiriamore','chiria more').replace('karunamayee','karunamoyee').replace('keshtopur','kestopur')
    return s
def nkey_core(s):
    k=nkey(s); k=GEN.sub(' ',k); return re.sub(r'\s+',' ',k).strip()
IDX_BASE={}; IDX_CORE={}   # normalised full name / name without generic words -> features
EXACT_KINDS={'station','halt','tram_stop','bus_station','ferry_terminal','platform','bus_stop','stop','hospital','college','university','cinema','theatre',
 'police','marketplace','courthouse','townhall','mall','park','stadium','garden','bridge','retail','commercial','industrial','pier','level_crossing'}
WORSHIP=re.compile(r'mandir|temple|masjid|mosque|church|kali|durga|shiva|mazar')
def build_index():
    import glob
    for fn in sorted(glob.glob(os.path.join(ROOT,'sources','raw','auto_fares','osm_nf_*.json'))):
        for e in json.load(open(fn)).get('elements',[]):
            t=e.get('tags',{})
            lat=e.get('lat') or (e.get('center') or {}).get('lat'); lon=e.get('lon') or (e.get('center') or {}).get('lon')
            if lat is None or not inbb(lat,lon): continue
            if t.get('railway') in ('subway_entrance',): continue
            kind=t.get('place') or t.get('railway') or t.get('amenity') or t.get('public_transport') or t.get('highway') or t.get('leisure') or t.get('tourism') or t.get('shop') or t.get('man_made') or t.get('landuse') or '?'
            f=dict(type=e['type'],id=e['id'],lat=lat,lon=lon,kind=kind,place=t.get('place'),name=t.get('name',''))
            for k in ('name','name:en','alt_name','old_name','official_name','short_name','int_name'):
                for v in t.get(k,'').split(';'):
                    v=v.strip()
                    if not v: continue
                    kb=nkey(v); kc=nkey_core(v)
                    if len(kb)>2: IDX_BASE.setdefault(kb,[]).append(f)
                    if len(kc)>2 and kc!=kb: IDX_CORE.setdefault(kc,[]).append(f)
    log('index keys base %d core %d'%(len(IDX_BASE),len(IDX_CORE)))
PLACE_RANK={'city':0,'town':1,'suburb':1,'quarter':1,'neighbourhood':2,'village':2,'hamlet':3,'locality':3,'isolated_dwelling':4}
CORE_PLACES={'suburb','quarter','neighbourhood','village','hamlet','locality'}  # city/town centroids are too coarse for a junction
def idx_candidates(name):
    base=nkey(name); core=nkey_core(name)
    want_station=bool(re.search(r'\b(station|stn|railway|rly)\b',base)) and 'police' not in base
    want_ghat='ghat' in base
    out=[]
    for f in IDX_BASE.get(base,[]):
        if f['place'] is None and f['kind'] not in EXACT_KINDS and not (f['kind']=='place_of_worship' and WORSHIP.search(base)): continue
        out.append(((0,PLACE_RANK.get(f['place'],5),0),f))
    if core and core!=base and 'police' not in base:
        for f in IDX_BASE.get(core,[])+IDX_CORE.get(core,[]):
            ok=(f['place'] in CORE_PLACES and not want_station and not want_ghat) \
               or (want_station and f['kind'] in ('station','halt','tram_stop')) \
               or (want_ghat and f['kind'] in ('ferry_terminal','pier'))
            if not ok: continue
            out.append(((1,0 if want_station else PLACE_RANK.get(f['place'],5),0 if (want_station and f['kind'] in ('station','halt')) else 1),f))
    out.sort(key=lambda x:x[0])
    res=[]
    for sc,f in out:
        if all(hav((f['lat'],f['lon']),(g['lat'],g['lon']))>0.15 for _,g in res): res.append((sc,f))
    return res

def name_ok(name,disp_first,category=''):
    a=nkey(name).replace('bazaar','bazar').replace(' stn',' station').replace(' rly ',' railway '); b=nkey(disp_first).replace('bazaar','bazar')
    if a==b: return True
    # a station query may match the bare locality name on a railway/transport feature
    if re.search(r'\bstation\b',a) and category in ('railway','public_transport') and nkey_core(name)==nkey_core(disp_first) and nkey_core(name): return True
    return False

# candidates for every name: list of dict(lat,lon,tier,src,note)
GENERIC={'ferry ghat','health','health institution','old post office','bus stand','b t road','health centre','kantakhal'}  # too generic to place on one spot
def candidates(n):
    c=[]
    if nkey(n) in GENERIC: return c
    if n in LEGACY_OK:  # earlier accepted Nominatim answer
        g=LEGACY_OK[n]; c.append(dict(lat=float(g['lat']),lon=float(g['lon']),tier=0,src='nominatim-legacy',
           note='coord: OSM Nominatim %s/%s (%s), name-matched, in route cluster'%(g['osm_type'],g['osm_id'],g.get('type'))))
    for s,f in idx_candidates(n):
        if s[0]==0: note='coord: OSM %s/%s (%s) named "%s", exact name match in Overpass named-feature index'%(f['type'],f['id'],f['kind'],f['name'])
        else: note='coord: OSM %s/%s (%s) named "%s", locality or station match after dropping generic words; position approximate'%(f['type'],f['id'],f['kind'],f['name'])
        c.append(dict(lat=f['lat'],lon=f['lon'],tier=1+s[0],src='osm-index',note=note))
    return c
def remote_candidates(n):
    """Nominatim (strict name equality) then Photon, only used when local sources gave nothing."""
    c=[]; q=Q.get(n,n)
    if nkey(n) in GENERIC: return c
    for qq,bounded in ((q,True),(q+' Kolkata',True)):
        for r in nom_raw(qq,bounded):
            first=r['display_name'].split(',')[0]
            if r.get('category') in ('highway','shop','craft','office') and r.get('type')!='bus_stop': continue  # a road or shop centroid is not a stop
            if name_ok(n,first,r.get('category','')) or name_ok(q,first,r.get('category','')):
                c.append(dict(lat=float(r['lat']),lon=float(r['lon']),tier=3,src='nominatim',
                    note='coord: OSM Nominatim %s/%s (%s) named "%s", normalised name equal'%(r['osm_type'],r['osm_id'],r.get('type'),first)))
        if c: break
    if not c:
        for r in photon(q):
            p=r.get('properties',{}); co=r['geometry']['coordinates']
            if p.get('osm_key') in ('shop','craft','office','highway') and p.get('osm_value')!='bus_stop': continue
            if p.get('osm_id') and name_ok(n,p.get('name','')) and inbb(co[1],co[0]):
                c.append(dict(lat=co[1],lon=co[0],tier=4,src='photon',
                    note='coord: OSM via Photon %s%s (%s) named "%s", normalised name equal'%({'N':'node/','W':'way/','R':'relation/'}.get(p.get('osm_type'),''),p['osm_id'],p.get('osm_value'),p.get('name'))))
    return c

LEGACY_BAD={'Orient','Purbachal','Blood Bank','Ayurvedic Hospital','Gauri Bari','Garia','Jora Mandir'}  # earlier manual rejects of Nominatim guesses
LEGACY_OK={}
def gather():
    build_index()
    old={}  # names that existed in the first release keep the earlier legacy answer when it passed review
    for n in names:
        g=best(n) if n in OLD_NAMES else None
        if g and n not in LEGACY_BAD: LEGACY_OK[n]=g
    cand={}
    for i,n in enumerate(names):
        c=candidates(n)
        if not c: c=remote_candidates(n)
        cand[n]=c
        if i%25==0: log('geocode %d/%d %s -> %d cand'%(i,len(names),n,len(c)))
    return cand

# ======================================================================
def resolve(cand):
    chosen={}; amb={}
    for n,c in cand.items():
        if not c: chosen[n]=None; continue
        top=min(x['tier'] for x in c)
        tops=[x for x in c if x['tier']==top]
        if all(hav((x['lat'],x['lon']),(tops[0]['lat'],tops[0]['lon']))<1.5 for x in tops): chosen[n]=tops[0]
        else: amb[n]=tops; chosen[n]=None
    partners={}
    for r in R:
        ss=[cn(s) for s in r['stops']]
        for s in ss: partners.setdefault(s,set()).update(p for p in ss if p!=s)
    for n,tops in amb.items():
        anc=[(chosen[p]['lat'],chosen[p]['lon']) for p in partners.get(n,()) if chosen.get(p)]
        if not anc: log('AMBIGUOUS no anchor, left blank: '+n); continue
        sc=sorted((sum(hav((t['lat'],t['lon']),a) for a in anc)/len(anc),i) for i,t in enumerate(tops))
        if sc[0][0]<8: chosen[n]=tops[sc[0][1]]; log('ambiguity resolved by route partners: %s (%.1f km mean)'%(n,sc[0][0]))
        else: log('AMBIGUOUS, nearest candidate %.1f km from partners, left blank: %s'%(sc[0][0],n))
    return chosen
LONG={'auto_garia_baruipur','auto_baruipur_dakshin_barasat','auto_baruipur_julpia','auto_sonarpur_garia'}
BOX_SOUTH=(22.40,88.24,22.72,88.50)   # Kolkata, Howrah, Salt Lake, New Town (official serials 1 to 296 and the first hand-built routes)
BOX_NORTH=(22.58,88.30,23.00,88.50)   # Dum Dum, Barrackpore, Barasat, Kalyani edge (official serials 297 to 489)
def route_box(r):
    if r['rid'] in LONG: return (22.2,88.0,22.8,88.6)
    if r['rid'].startswith('auto_rta_n24p'): return BOX_NORTH
    m=re.match(r'auto_rta_(\d+)',r['rid'])
    if m and int(m.group(1))>=297: return BOX_NORTH
    return BOX_SOUTH
def in_box(c,b): return b[0]<=c['lat']<=b[2] and b[1]<=c['lon']<=b[3]
def plausibility(chosen):
    """Reject coordinates that are outside the route's expected area or put its stops implausibly far apart.
    When two stops of one route are too far apart and we cannot tell which is wrong, the one that is not
    corroborated by other routes is rejected; if both are equally uncorroborated both are rejected."""
    rejected=set()
    for _ in range(80):
        blame={}; support={}
        for r in R:
            box=route_box(r)
            pts=[(cn(s),chosen[cn(s)]) for s in r['stops'] if chosen.get(cn(s)) and cn(s) not in rejected]
            for n,c in pts:
                if not in_box(c,box): blame[n]=blame.get(n,0)+1000
            pts=[(n,c) for n,c in pts if in_box(c,box)]
            if len(pts)<2: continue
            if len(pts)>=3:
                ml=sorted(p[1]['lat'] for p in pts)[len(pts)//2]; mo=sorted(p[1]['lon'] for p in pts)[len(pts)//2]
                for n,c in pts:
                    d=hav((ml,mo),(c['lat'],c['lon']))
                    if d>(30 if r['rid'] in LONG else 8): blame[n]=blame.get(n,0)+d
                    else: support[n]=support.get(n,0)+1
            else:
                d=hav((pts[0][1]['lat'],pts[0][1]['lon']),(pts[1][1]['lat'],pts[1][1]['lon']))
                lim2=40 if r['rid'] in LONG else (15 if box==BOX_NORTH else 8)   # south and central autos run short stretches
                if d>lim2:
                    for n,c in pts: blame[n]=blame.get(n,0)+d
                else:
                    for n,c in pts: support[n]=support.get(n,0)+1
        if not blame: break
        scaled={n:(v if v>=1000 else v/(1+support.get(n,0))) for n,v in blame.items()}
        top=max(scaled.values())
        for n,v in scaled.items():
            if v>=top*0.99: rejected.add(n); log('PLAUSIBILITY reject %s (score %.1f, corroborated by %d routes)'%(n,v,support.get(n,0)))
    return rejected

def main():
    cand=gather()
    chosen=resolve(cand)
    rej=plausibility(chosen)
    os.makedirs(ROOT+'/data/auto',exist_ok=True)
    srcof={}
    for r in R:
        for s in r['stops']: srcof.setdefault(cn(s),set()).update(r['src'].split('|'))
    ng=0; bysrc={}
    with open(ROOT+'/data/auto/stops.csv','w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['stop_id','stop_name','lat','lon','source_id','notes'])
        for n in names:
            g=chosen.get(n) if n not in rej else None
            srcs='|'.join(sorted(srcof[n]))
            if g:
                ng+=1; bysrc[g['src']]=bysrc.get(g['src'],0)+1
                w.writerow(['auto_'+slug(n),n,'%.6f'%g['lat'],'%.6f'%g['lon'],srcs,g['note']])
            else:
                why='not geocoded: no confident OSM match' if not cand.get(n) else 'not geocoded: OSM match rejected by plausibility or ambiguity checks'
                w.writerow(['auto_'+slug(n),n,'','',srcs,why])
    with open(ROOT+'/data/auto/routes.csv','w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow('route_id,route_name,mode,operator,origin,destination,headway_min_peak,headway_min_offpeak,first_service,last_service,fare_min_inr,fare_max_inr,source_id,confidence,notes'.split(','))
        for r in R:
            fm=r['fmin']; w.writerow([r['rid'],'%s - %s'%(r['o'],r['d']),'auto','',r['o'],r['d'],'','','','',fm,fm,r['src'],r['conf'],r['notes']])
    with open(ROOT+'/data/auto/route_stops.csv','w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['route_id','direction','seq','stop_id','travel_min_from_start'])
        for r in R:
            for i,s in enumerate(r['stops'],1): w.writerow([r['rid'],0,i,'auto_'+slug(cn(s)),''])
    log('routes %d stops %d located %d by source %s'%(len(R),len(names),ng,bysrc))
    usable=0
    for r in R:
        if sum(1 for s in r['stops'] if chosen.get(cn(s)) and cn(s) not in rej)>=2: usable+=1
    log('routes with >=2 located stops (will survive GTFS build): %d'%usable)
if __name__=='__main__': main()

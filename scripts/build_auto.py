import csv, re, json, time, difflib, math, urllib.request, urllib.parse, os, sys
ROOT='/mnt/DATA/projects/calcutta-transport'
UA='calcutta-transport-personal-research/0.1 (personal non-commercial transit dataset)'
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
add('auto_r44','Kadapara','Machua',CI+'|'+F3,'community','Route no 44; via Narkeldanga Main Rd, Keshab Chandra Sen St, Madan Mohan Burman St',
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
# normalise stop names to canonical
CANON={'Howrah Station':'Howrah Station','Tank No 12 (Salt Lake)':'Tank No 12 Salt Lake','Tank No 13 (Salt Lake)':'13 No. Tank','Sector V (Salt Lake)':'Sector V','Bikash Bhavan (Salt Lake)':'Bikash Bhavan','Karunamoyee (Salt Lake)':'Karunamoyee','SDF (Salt Lake)':'SDF Building Salt Lake','CIT Building More':'CIT Building More'}
# route 59 / bigger-stop name aliases for geocode query
Q={'Phoolbagan':'Phoolbagan','Karunamoyee':'Karunamoyee','Ballygunge Station':'Ballygunge railway station','Sealdah Station':'Sealdah railway station','Howrah Station':'Howrah railway station','Behala Tram Depot':'Behala Tram Depot','Gariahat':'Gariahat','Judges Court':'Judges Court Road','MG Road Metro':'M G Road metro station','Kalighat Metro':'Kalighat metro station','Girish Park':'Girish Park','RG Kar Hospital':'R G Kar Medical College','ID Hospital':'Infectious Diseases Hospital Beliaghata','Botanical Garden':'Acharya Jagadish Chandra Bose Indian Botanic Garden','Sector V':'Sector V Salt Lake','Bikash Bhavan':'Bikash Bhavan Salt Lake','Sarsuna':'Sarsuna','Chingrighata':'Chingrighata','SDF Building Salt Lake':'SDF Building Salt Lake','Behala Chowrasta':'Behala Chowrasta'}
def cn(n): return CANON.get(n,n)
# --- geocode
cache_f=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','sources','cache','nominatim_cache.json')
cache=json.load(open(cache_f)) if os.path.exists(cache_f) else {}
BB=(88.15,22.30,88.55,22.95) # left,bottom,right,top (greater Kolkata incl Baruipur/Barasat)
def nom(q):
    if q in cache: return cache[q]
    p=urllib.parse.urlencode(dict(q=q+', Kolkata',format='jsonv2',limit=5,viewbox='%s,%s,%s,%s'%(BB[0],BB[3],BB[2],BB[1]),bounded=1,addressdetails=0,countrycodes='in'))
    req=urllib.request.Request('https://nominatim.openstreetmap.org/search?'+p,headers={'User-Agent':UA})
    try: res=json.load(urllib.request.urlopen(req,timeout=30))
    except Exception as e: res=[]; print('ERR',q,e,file=sys.stderr)
    cache[q]=res; json.dump(cache,open(cache_f,'w')); time.sleep(1.1); return res
def sim(a,b):
    a,b=a.lower(),b.lower(); return difflib.SequenceMatcher(None,a,b).ratio()
STOP={'station','more','crossing','road','rd','park','hospital','cinema'}
def best(name):
    q=Q.get(name,name); res=nom(q); out=None
    toks=[t for t in re.findall(r'[a-z0-9]+',name.lower()) if t not in STOP and len(t)>2] or re.findall(r'[a-z0-9]+',name.lower())
    for r in res:
        first=r['display_name'].split(',')[0]
        full=r['display_name'].lower()
        okname = sim(name,first)>=0.6 or sim(q,first)>=0.6 or all(t in first.lower() for t in toks)
        if okname and 'kolkata' in full or okname and ('howrah' in full or 'north 24' in full or 'south 24' in full):
            return r
    return None
names=[]
for r in R:
    for s in r['stops']:
        c=cn(s)
        if c not in names: names.append(c)
print(len(names),'unique stops',file=sys.stderr)
geo={}
for i,n in enumerate(names):
    r=best(n); geo[n]=r
    print(i,n,'->',(r['display_name'][:70] if r else None),file=sys.stderr)
# route-level plausibility: drop a coord > 6 km from median of the route's coords
def hav(a,b):
    R_=6371;la1,lo1,la2,lo2=map(math.radians,[a[0],a[1],b[0],b[1]])
    h=math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R_*math.asin(math.sqrt(h))
bad={'Orient','Purbachal','Blood Bank','Ayurvedic Hospital','Gauri Bari','Garia','Jora Mandir'}  # manual rejects after review
for r in R:
    pts=[(float(geo[cn(s)]['lat']),float(geo[cn(s)]['lon']),cn(s)) for s in r['stops'] if geo.get(cn(s))]
    if len(pts)>=3:
        ml=sorted(p[0] for p in pts)[len(pts)//2]; mo=sorted(p[1] for p in pts)[len(pts)//2]
        lim=30 if r['rid'] in ('auto_garia_baruipur','auto_baruipur_dakshin_barasat','auto_baruipur_julpia','auto_sonarpur_garia') else 8
        for p in pts:
            if hav((ml,mo),(p[0],p[1]))>lim: bad.add(p[2]); print('REJECT far',r['rid'],p[2],file=sys.stderr)
os.makedirs(ROOT+'/data/auto',exist_ok=True)
# ---- write stops
srcof={}
for r in R:
    for s in r['stops']:
        srcof.setdefault(cn(s),set()).update(r['src'].split('|'))
with open(ROOT+'/data/auto/stops.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['stop_id','stop_name','lat','lon','source_id','notes'])
    ng=0
    for n in names:
        g=geo[n] if n not in bad else None
        srcs='|'.join(sorted(srcof[n]))
        if g:
            ng+=1; w.writerow(['auto_'+slug(n),n,'%.6f'%float(g['lat']),'%.6f'%float(g['lon']),srcs,'coord: OSM Nominatim %s/%s (%s), name-matched, in route cluster'%(g['osm_type'],g['osm_id'],g.get('type'))])
        else: w.writerow(['auto_'+slug(n),n,'','',srcs,'not geocoded: no confident OSM match'])
print('geocoded',ng,'of',len(names),file=sys.stderr)
with open(ROOT+'/data/auto/routes.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow('route_id,route_name,mode,operator,origin,destination,headway_min_peak,headway_min_offpeak,first_service,last_service,fare_min_inr,fare_max_inr,source_id,confidence,notes'.split(','))
    for r in R:
        fm=r['fmin']; w.writerow([r['rid'],'%s - %s'%(r['o'],r['d']),'auto','',r['o'],r['d'],'','','','',fm,fm,r['src'],r['conf'],r['notes']])
with open(ROOT+'/data/auto/route_stops.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['route_id','direction','seq','stop_id','travel_min_from_start'])
    for r in R:
        for i,s in enumerate(r['stops'],1): w.writerow([r['rid'],0,i,'auto_'+slug(cn(s)),''])
print('routes',len(R))

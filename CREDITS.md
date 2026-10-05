# Credits

This dataset is compiled from public information published by the people and organisations below. Route facts (numbers, termini, stop order, timings, fares) were extracted and normalised; no source text, tables or files are redistributed. Full source details, fetch dates and licence notes are in [sources/SOURCES.md](sources/SOURCES.md). Where a source states no open licence, only facts are used, with the credit below; that is not a claim of permission from the source.

## Maps, coordinates and place names

- **OpenStreetMap contributors**: stop and station coordinates, route lines and stop order checks, ferry crossings, bus route relations. © OpenStreetMap contributors, [ODbL 1.0](https://www.openstreetmap.org/copyright). Accessed through the [Overpass API](https://overpass-api.de/), [Nominatim](https://nominatim.org/) and the [Geofabrik](https://download.geofabrik.de/) regional extract.
- **Photon** by [komoot](https://photon.komoot.io/) (OpenStreetMap data, ODbL): queried as a last-resort geocoder for auto stops; no coordinate in the current data comes from it.
- **GeoNames** ([geonames.org](https://www.geonames.org/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)): place coordinates for some bus stops (`coord_method=geonames`).
- **Wikidata** ([CC0](https://creativecommons.org/publicdomain/zero/1.0/)): place coordinates for some bus stops and evidence for the aliases in `data/bus/stop_aliases.csv`.
- **Wikipedia contributors** ([CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), facts only): former and current place names behind `data/bus/stop_aliases.csv` (for example Dalhousie Square = B. B. D. Bagh), and the Kolkata Metro, Kolkata Suburban Railway and Trams in Kolkata articles for station lists, service statements and fare tables.

## Bus and minibus

- **West Bengal Transport Corporation (WBTC)**: [city bus route list](https://wbtconline.in/wbtc-city-bus-routes).
- **Kolkata Bus-O-Pedia Foundation**: [private](https://www.kolbusopedia.com/bus-routes) and [government](https://www.kolbusopedia.com/bus-routes-government) route catalogues, the backbone of the bus layer, and AC bus fare notes. Thank you.
- **BusBondhu** by Rounak Adhikary: consulted to locate the upstream sources above. No BusBondhu data is included.
- Fare and service reporting: **wbxpress.com** (2018 bus fare notification), **ThePrint**, **Asianet News Bangla**, **Sangbad Pratidin**, **Ei Muhurte**, **Naya Bharat 24x7**.

## Metro and suburban rail

- **Metro Railway, Kolkata**: [official timetable PDFs and pages](https://mtp.indianrailways.gov.in/view_section.jsp?lang=0&id=0,2,630) for the Blue, Green, Yellow, Purple and Orange lines.
- **erail.in** (Indian Railways timetable and run-days data as published there): suburban train timetables, stop lists and run days.
- Metro smart card and fare reporting: **The Week** (PTI wire), **Millennium Post**, **NewKerala**, **Business Standard**, **Deccan Herald**; aggregators **kolkatametrorail.com**, **kolkatametro.org**, **metrofare.in** and **yometro.com** (cross-checks only).
- Rail fares and season tickets: **Train Help**, **The Statesman** (via the [Internet Archive](https://web.archive.org/)).

## Ferry and tram

- **West Bengal Transport Department** and **WBTC**: ferry route list, ferry fare list, the 2020 vessel order, and the tram note (all-day ticket, concessions).
- Ferry guides: **Traveling Creature**, **Kolkata Dekho**, **Kolkata On Wheels**, **yappe.in**, **Column Phase**, **The Holiday Story**, **Tripoto**.
- Tram: **The Week** (2026 tram report), **KolkataOnline** (fares), **Kolkata Tales** (AC tram fare and 2026 departure windows), **Millennium Post** (2020), **wikiroutes.info**, **Calcutta Tramways Company** site (heritage fare).

## Autos and other fares

- **RTA Kolkata, Transport Department, Government of West Bengal**: notification 5673-WT of 10 Dec 2018 (489 auto routes), read from the page images on **wbxpress.com**; also the Auto-Rickshaw Policy 2018 and the 2018 taxi fare notification on wbxpress.com.
- **District Administration, Howrah**: notice for a new auto route (Chawal Patty to Belanagar).
- **Kolkata City Info** and **F3Kolkata** (via the [Internet Archive](https://web.archive.org/)): auto routes and stops.
- Auto route and fare reporting: **Bartaman**, **Aaj Tak Bangla**, **Ei Muhurte**, **Subkuz**, **Banglahunt**, **IndiaHood**, **Millennium Post** (RTA North 24 Parganas permits).

## Tools

- [MobilityData GTFS Validator](https://github.com/MobilityData/gtfs-validator) for feed validation (see [docs/VALIDATION.md](docs/VALIDATION.md)).

If you own one of these sources and want the credit changed or your data removed, open an issue.

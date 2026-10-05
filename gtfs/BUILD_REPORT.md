# GTFS build report

Built 20261006 by `scripts/build_gtfs.py`. Calendar valid 20261006 to 20271006. Generated file, do not edit.

## Feed totals

| file | rows |
|---|---|
| routes.txt | 801 |
| trips.txt | 3199 |
| stop_times.txt | 48344 |
| frequencies.txt | 1606 |
| stops.txt | 1800 |
| transfers.txt | 56 |
| fare_attributes.txt | 5 |
| calendar.txt | 11 |

## Per mode

| mode | routes in | routes out | routes dropped | stops in | stops unlocated | stops in feed | trips | notes |
|---|---|---|---|---|---|---|---|---|
| metro | 5 | 5 | 0 | 57 | 0 | 57 | 24 | route_stop_rows_in=114, directions_with_given_times=6, directions_estimated_times=4 |
| rail | 27 | 27 | 0 | 355 | 0 | 355 | 1637 |  |
| bus | 605 | 603 | 2 | 2362 | 1185 | 1177 | 1206 | of_which_minibus=85, route_stop_rows_in=23688, route_stop_rows_dropped_unlocated=6118, directions_dropped_lt2=4, directions_estimated_times=1206, hops_over_20km=100 |
| tram | 2 | 2 | 0 | 12 | 0 | 12 | 4 | route_stop_rows_in=28, directions_estimated_times=4 |
| ferry | 10 | 8 | 1 | 17 | 2 | 15 | 16 | route_stop_rows_in=42, route_stop_rows_dropped_unlocated=4, directions_dropped_lt2=2, directions_with_given_times=11, directions_estimated_times=5, routes_excluded_no_service_data=1 |
| auto | 514 | 156 | 358 | 661 | 419 | 184 | 312 | route_stop_rows_in=1164, route_stop_rows_dropped_unlocated=538, directions_dropped_lt2=358, directions_mirrored=156, directions_estimated_times=312 |

Stops in feed counts only located stops that at least one trip uses.

## Modelling and defaults

- route_type: metro 1, rail 2, bus 3, minibus 3 (agency: private minibus operators), tram 0, ferry 4, share auto 3 (agency: share auto route operators). Extended types (1501 communal taxi, 715 demand responsive) were not used: share autos run fixed routes without booking, and basic type 3 is understood by every consumer, while extended types are not.
- Rail: real trips from data/rail/timetable.csv; one service per days mask (Mon..Sun order, confirmed by the Saturday-only train HWH-BWN LOCAL SAO having mask 0000010). Masks: 0000001 x2, 0000010 x4, 1111010 x2, 1111100 x6, 1111101 x3, 1111110 x241, 1111111 x1379.
- Other modes: one template trip per route, direction and service period, frequencies.txt with exact_times=0. Peak headway 07:00-10:00 and 17:00-20:00, off-peak elsewhere. Each window ends 60 s after the last departure so that departure is included.
- Service days: routes listed in data/{mode}/service_periods.csv get one trip per days mask and direction (services: daily = 1111111, saturday = 0000010, sunday = 0000001, weekdays = 1111100; masks are Mon..Sun). Routes using it: metro: metro_blue, metro_green, metro_purple, metro_orange, metro_yellow. Every other non-rail route runs daily on its routes.csv window.
- Defaults are applied only to auto, bus, minibus routes, where service is known to run all day but no source publishes headways or hours: headway auto 10 min, bus 15 min, minibus 15 min, window 06:00 to 21:00. Metro, ferry and tram routes without a sourced headway and window are left out of the feed (listed below), so no service is invented.
- Estimated stop times: straight-line distance x 1.3 at bus 15 km/h, minibus 15 km/h, auto 18 km/h, tram 10 km/h, ferry 10 km/h, metro 33 km/h, at least 30 s per hop; travel_min_from_start values are used as anchors where present and the estimate is interpolated between them. Estimated times have timepoint=0.
- Stops without coordinates are removed from each stop sequence; a direction with fewer than 2 located stops is dropped, and a route with no direction left is dropped.
- Share auto data has direction 0 only; the builder adds the reverse sequence as direction 1 (MIRROR_AUTO), since route autos shuttle both ways.
- shapes.txt is not produced (no cheap, reliable geometry for all modes).

## Routes using defaults

- headway default (both columns blank): 759 routes (auto 156, bus 603)
- one headway column blank, other copied: 0 routes (none)
- first_service default 06:00: 759 routes (auto 156, bus 603)
- last_service default 21:00: 759 routes (auto 156, bus 603)

Bus, minibus and auto routes with a default are all routes of those modes that are in the feed: no source publishes their frequencies or hours.

## Routes left out for lack of service data

- ferry: ferry_rsv_circuit: no sourced headway and service window in routes.csv or service_periods.csv; kept in data/, not in the feed

## Dropped routes

- metro: 0
- rail: 0
- bus: 2: bus_mn2, bus_ac10
- tram: 0
- ferry: 1: ferry_ahiritola_bandhaghat
- auto: 358

## Transfers

28 stop pairs (both directions written, transfer_type 2): metro, rail, ferry and tram stops with matching names within 600 m, plus the reviewed pairs in data/interchanges.csv (up to 1000 m). Minimum transfer time is 2 min plus the straight-line walk at 1 m/s.

- Ahiritola Ghat (ferry_ahiritola) and Sovabazar Ahiritola (rail_sovabazar_ahiritola): 209 m, min 360 s (interchanges.csv)
- Fairlie Ghat (Fairlie Place) (ferry_fairlie) and BBD Bagh (rail_bbd_bagh): 129 m, min 300 s (interchanges.csv)
- Howrah Station Ghat (Howrah Launch Ghat) (ferry_howrah) and Howrah (metro_howrah): 381 m, min 540 s (name match)
- Shobhabazar Ghat (ferry_shobhabazar) and Sovabazar Ahiritola (rail_sovabazar_ahiritola): 260 m, min 420 s (interchanges.csv)
- Dakshineswar (metro_dakshineswar) and Dakshineswar Ghat (ferry_dakshineswar): 638 m, min 780 s (interchanges.csv)
- Dakshineswar (metro_dakshineswar) and Dakshineshwar (rail_dakshineshwar): 20 m, min 180 s (interchanges.csv)
- Esplanade (metro_esplanade_green) and Esplanade (metro_esplanade_blue): 107 m, min 240 s (name match)
- Esplanade (metro_esplanade_green) and Esplanade (tram_esplanade): 130 m, min 300 s (name match)
- Kavi Subhash (metro_kavi_subhash_orange) and New Garia (rail_new_garia): 188 m, min 360 s (interchanges.csv)
- Majerhat (metro_majerhat) and Majherhat (rail_majherhat): 155 m, min 300 s (interchanges.csv)
- Noapara (metro_noapara_yellow) and Noapara (metro_noapara_blue): 17 m, min 180 s (name match)
- Rabindra Sarobar (metro_rabindra_sarobar) and Tollygunge (rail_tollygunge): 236 m, min 360 s (interchanges.csv)
- Bagbazar (rail_bagbazar) and Bagbazar Launch Ghat (ferry_bagbazar): 194 m, min 360 s (name match)
- Baranagar Road (rail_baranagar_road) and Baranagar (metro_baranagar): 191 m, min 360 s (name match)
- BBD Bagh (rail_bbd_bagh) and Millennium Park (Shipping) Jetty (ferry_millennium_park): 546 m, min 720 s (interchanges.csv)
- Belur Math (rail_belur_math) and Belur Math Ghat (ferry_belur_math): 839 m, min 960 s (interchanges.csv)
- Dakshineshwar (rail_dakshineshwar) and Dakshineswar Ghat (ferry_dakshineswar): 637 m, min 780 s (interchanges.csv)
- Dum Dum Junction (rail_dum_dum) and Dum Dum (metro_dum_dum): 118 m, min 240 s (name match)
- Dum Dum Cantonment (rail_dum_dum_cantonment) and Dum Dum Cantonment (metro_dum_dum_cantonment): 123 m, min 300 s (name match)
- Eden Gardens (rail_eden_gardens) and Babughat / Chandpal Ghat (ferry_babughat): 529 m, min 660 s (interchanges.csv)
- Howrah Junction (rail_howrah) and Howrah Station Ghat (Howrah Launch Ghat) (ferry_howrah): 103 m, min 240 s (name match)
- Howrah Junction (rail_howrah) and Howrah (metro_howrah): 277 m, min 420 s (name match)
- Sealdah (rail_sealdah) and Sealdah (metro_sealdah): 201 m, min 360 s (name match)
- Seoraphuli Jn (rail_seoraphuli) and Sheoraphuli Ghat (ferry_sheoraphuli): 331 m, min 480 s (interchanges.csv)
- Serampore (rail_serampore) and Jugal Auddy Ferry Ghat (west bank, Serampore side) (ferry_jugal_auddy_ghat): 647 m, min 780 s (interchanges.csv)
- Esplanade (tram_esplanade) and Esplanade (metro_esplanade_blue): 138 m, min 300 s (name match)
- Park Circus (tram_park_circus) and Park Circus - Seven Point (tram_park_circus_seven_point): 99 m, min 240 s (name match)
- Shyambazar (tram_shyambazar) and Shyambazar (metro_shyambazar): 168 m, min 300 s (name match)

## Fares

GTFS fares v1 written only for ferry routes whose fare_min_inr equals fare_max_inr (5 routes, one flat fare per crossing, no transfers). Share auto routes with one fare value are not encoded: that value is the end-to-end fare and autos charge less for shorter stages. Bus and minibus slabs, metro slabs and rail slabs are distance based and need along-track km between every stop pair, which the data does not carry; they are documented in data/fares/ and data/{metro,rail}/fares.csv but not encoded in the feed.

## Warnings

- none

## Licence and attribution

- feed_publisher_url and feed_contact_url: https://github.com/arkodeepg/calcutta-transport.
- attributions.txt credits the publisher and the data sources (OpenStreetMap contributors under ODbL, GeoNames CC BY 4.0, Wikidata, Wikipedia, Metro Railway Kolkata, erail.in, Bus-O-Pedia, WBTC, RTA Kolkata).
- The zip also carries LICENSE-DATA.txt (ODbL 1.0 full text) and ATTRIBUTION.txt (the ODbL and OpenStreetMap notice). These two are not GTFS files; validators report them as unknown files.


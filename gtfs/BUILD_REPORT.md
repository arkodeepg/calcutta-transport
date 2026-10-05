# GTFS build report

Built 20261005 by `scripts/build_gtfs.py`. Calendar valid 20261005 to 20271005. Generated file, do not edit.

## Feed totals

| file | rows |
|---|---|
| routes.txt | 652 |
| trips.txt | 2887 |
| stop_times.txt | 43762 |
| frequencies.txt | 1270 |
| stops.txt | 1300 |
| transfers.txt | 28 |
| fare_attributes.txt | 5 |
| calendar.txt | 8 |

## Per mode

| mode | routes in | routes out | routes dropped | stops in | stops unlocated | stops in feed | trips | notes |
|---|---|---|---|---|---|---|---|---|
| metro | 5 | 5 | 0 | 57 | 0 | 57 | 10 | route_stop_rows_in=114, directions_with_given_times=6, directions_estimated_times=4 |
| rail | 27 | 27 | 0 | 355 | 0 | 355 | 1637 |  |
| bus | 605 | 584 | 21 | 2304 | 1499 | 805 | 1168 | of_which_minibus=85, route_stop_rows_in=23688, route_stop_rows_dropped_unlocated=9948, directions_dropped_lt2=42, directions_estimated_times=1168, suspect_stop_spikes=31, hops_over_20km=103 |
| tram | 2 | 2 | 0 | 12 | 0 | 12 | 4 | route_stop_rows_in=28, directions_estimated_times=4 |
| ferry | 10 | 9 | 1 | 17 | 2 | 15 | 18 | route_stop_rows_in=50, route_stop_rows_dropped_unlocated=4, directions_dropped_lt2=2, directions_with_given_times=11, directions_estimated_times=7 |
| auto | 31 | 25 | 6 | 117 | 60 | 56 | 50 | route_stop_rows_in=196, route_stop_rows_dropped_unlocated=90, directions_dropped_lt2=6, directions_mirrored=25, directions_estimated_times=50 |

Stops in feed counts only located stops that at least one trip uses.

## Modelling and defaults

- route_type: metro 1, rail 2, bus 3, minibus 3 (agency: private minibus operators), tram 0, ferry 4, share auto 3 (agency: share auto route operators). Extended types (1501 communal taxi, 715 demand responsive) were not used: share autos run fixed routes without booking, and basic type 3 is understood by every consumer, while extended types are not.
- Rail: real trips from data/rail/timetable.csv; one service per days mask (Mon..Sun order, confirmed by the Saturday-only train HWH-BWN LOCAL SAO having mask 0000010). Masks: 0000001 x2, 0000010 x4, 1111010 x2, 1111100 x6, 1111101 x3, 1111110 x241, 1111111 x1379.
- Other modes: one template trip per route direction, frequencies.txt with exact_times=0. Peak headway 07:00-10:00 and 17:00-20:00, off-peak elsewhere. The same calendar (daily) is used for all days; weekend differences (e.g. metro Sunday 09:00 start) are not modelled.
- Default headways when both headway columns are blank: bus 15 min, minibus 15 min, auto 10 min, tram 20 min, ferry 20 min, metro 10 min. Default service window when first/last is blank: 06:00 to 21:00.
- Estimated stop times: straight-line distance x 1.3 at bus 15 km/h, minibus 15 km/h, auto 18 km/h, tram 10 km/h, ferry 10 km/h, metro 33 km/h, at least 30 s per hop; travel_min_from_start values are used as anchors where present and the estimate is interpolated between them. Estimated times have timepoint=0.
- Stops without coordinates are removed from each stop sequence; a direction with fewer than 2 located stops is dropped, and a route with no direction left is dropped.
- Share auto data has direction 0 only; the builder adds the reverse sequence as direction 1 (MIRROR_AUTO), since route autos shuttle both ways.
- shapes.txt is not produced (no cheap, reliable geometry for all modes).

## Routes using defaults

- headway default (both columns blank): 615 routes (auto 25, bus 584, ferry 4, tram 2)
- one headway column blank, other copied: 0 routes (none)
- first_service default 06:00: 613 routes (auto 25, bus 584, ferry 4)
- last_service default 21:00: 614 routes (auto 25, bus 584, ferry 4, metro 1)

## Dropped routes

- metro: 0
- rail: 0
- bus: 21: bus_s34b, bus_23, bus_64, bus_70, bus_72_2, bus_72a, bus_76, bus_77, bus_78_2, bus_92, bus_92a, bus_96c, bus_96c_2, bus_dn12, bus_dn38, bus_mm7, bus_mn2, bus_ac10, bus_d7, bus_d8, bus_d9
- tram: 0
- ferry: 1: ferry_ahiritola_bandhaghat
- auto: 6: auto_r57, auto_ultadanga_sector_v, auto_ultadanga_tank_12, auto_garia_baruipur, auto_sonarpur_garia, auto_baruipur_dakshin_barasat

## Transfers

14 stop pairs (both directions written, transfer_type 2) among metro, rail, ferry and tram stops with matching names within 600 m:

- Bagbazar Launch Ghat (ferry_bagbazar) and Bagbazar (rail_bagbazar): 194 m, min 360 s
- Howrah Station Ghat (Howrah Launch Ghat) (ferry_howrah) and Howrah (metro_howrah): 381 m, min 540 s
- Howrah Station Ghat (Howrah Launch Ghat) (ferry_howrah) and Howrah Junction (rail_howrah): 103 m, min 240 s
- Baranagar (metro_baranagar) and Baranagar Road (rail_baranagar_road): 191 m, min 360 s
- Esplanade (metro_esplanade_blue) and Esplanade (tram_esplanade): 138 m, min 300 s
- Esplanade (metro_esplanade_green) and Esplanade (metro_esplanade_blue): 107 m, min 240 s
- Esplanade (metro_esplanade_green) and Esplanade (tram_esplanade): 130 m, min 300 s
- Howrah (metro_howrah) and Howrah Junction (rail_howrah): 277 m, min 420 s
- Noapara (metro_noapara_yellow) and Noapara (metro_noapara_blue): 17 m, min 180 s
- Sealdah (metro_sealdah) and Sealdah (rail_sealdah): 201 m, min 360 s
- Shyambazar (metro_shyambazar) and Shyambazar (tram_shyambazar): 168 m, min 300 s
- Dum Dum Junction (rail_dum_dum) and Dum Dum (metro_dum_dum): 118 m, min 240 s
- Dum Dum Cantonment (rail_dum_dum_cantonment) and Dum Dum Cantonment (metro_dum_dum_cantonment): 123 m, min 300 s
- Park Circus (tram_park_circus) and Park Circus - Seven Point (tram_park_circus_seven_point): 99 m, min 240 s

## Fares

GTFS fares v1 written only for ferry routes whose fare_min_inr equals fare_max_inr (5 routes, one flat fare per crossing, no transfers). Share auto routes with one fare value are not encoded: that value is the end-to-end fare and autos charge less for shorter stages. Bus and minibus slabs, metro slabs and rail slabs are distance based and need along-track km between every stop pair, which the data does not carry; they are documented in data/fares/ and data/{metro,rail}/fares.csv but not encoded in the feed.

## Warnings

- bus_14a: stop bus_joramandir (Joramandir) is 7 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_ac4b: stop bus_karunamoyee (Karunamoyee) is 16 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_d1a: stop bus_birati (Birati) is 6 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_m7b: stop bus_karunamoyee (Karunamoyee) is 16 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_s47: stop bus_rampur (Rampur) is 25 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_21: stop bus_muchipara (Muchipara) is 12 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_85: stop bus_ichapur_more (Ichapur More) is 39 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_211_yellow_board: stop bus_bishnupur (Bishnupur) is 36 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_241: stop bus_bartala_bazar (Bartala Bazar) is 11 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_m10: stop bus_belgachi (Belgachi) is 34 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_m10: stop bus_kashinagar (Kashinagar) is 63 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_m15: stop bus_haridashpur (Haridashpur) is 117 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_sd3: stop bus_kalikapur (Kalikapur) is 12 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_sd63: stop bus_chandkhali (Chandkhali) is 30 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_dn16_1: stop bus_airport_covers_all_4_gates (Airport (Covers All 4 Gates)) is 21 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_dn28: stop bus_biswanathpur (Biswanathpur) is 65 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_kb15: stop bus_beltala (Beltala) is 26 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_mm3: stop bus_talikhola (Talikhola) is 59 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_mm3: stop bus_bengal_chemical (Bengal Chemical) is 13 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_e43: stop bus_barda (Barda) is 55 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_sta_bandar_howrah_station: stop bus_purshura (Purshura) is 53 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_sta_barasat_baruipur: stop bus_gazipur (Gazipur) is 25 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_sta_gadiara_rajarhat: stop bus_santragachi (Santragachi) is 9 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_sta_rajabazar_hasnabad: stop bus_kantaltala (Kantaltala) is 51 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_sta_shyampur_kolkata_station: stop bus_library_more (Library More) is 38 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_sta_shyampur_kolkata_station: stop bus_birshibpur (Birshibpur) is 30 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_st31: stop bus_bajkul (Bajkul) is 78 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_st31: stop bus_heria (Heria) is 66 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_e29: stop bus_santragachi (Santragachi) is 8 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_e57: stop bus_phulia (Phulia) is 57 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)
- bus_e70: stop bus_basantapur (Basantapur) is 34 km off the line between its neighbours (out-and-back spike, likely wrong coordinate)

## Notes

- feed_publisher_url is set to https://github.com/arkodeepg/calcutta-transport; replace it with the project URL once one is published (`--publisher-url`).


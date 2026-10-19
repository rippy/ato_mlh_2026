# All Things Open, Major League Hacking, 2026 Hackathon

Today's Event:

* <https://www.mlh.com/events/hacktoberfest-hacks-all-things-open/>

Our project:

* <https://github.com/rippy/ato_mlh_2026>

## MLH

* 5,000,000 people
* 9:30 Team Formation
* 1:00 Lunch
* 2:00 Projects Due
* 2:30 Presentations (video required to win)
* 4:15 Winners announced
* 7:30 Happy Hour at Sitti

Submit here:

* <https://www.mlh.com/events/hacktoberfest-hacks-all-things-open>

### MLH: Rewards

* 1st Place
  * $500, Arduino Uno Q, and MLH Winner Pins

* DigitalOcean Credits
  * <https://www.mlh.com/partners/digitalocean>

## Hacktoberfest

<https://hacktoberfest.com/>

* 500 in-person events all over the world, 85 next weekend alone
* DigitalOcean is giving us access to free open source
* Feedback and Happy hour later

## Tools

* <https://github.com>
* <https://formulae.brew.sh/cask/antigravity-cli>
* <https://gemini.google.com/app>
* <https://claude.ai/new>

Data Links

* <https://data.raleighnc.gov/>
* <https://www.openstreetmap.org/>

Ideas

* Density maps for good options for gluten-free options, vegan, etc (to help find a good place to live)

## MVP Idea

* Cookies or Session Local to record your address, to help with lookups

## APIs

### APIs: From Haiku

### APIs: From Beckers

Three datasets:  

* Restaurants evaulated in Wake County, has info on where it is located, and has the HSISIDD to match to the other datasets
  * Set of results by Postal Code and find close to you
* Food Inspections by Date
* Food Inspection Violations the Violations found
  * Trending up, trending down, how long ago, etc
  * Immuno compromised will probably not want poor scores

## DigitalOcean

* With the credits, can use open weight models cheaply

## Raw Notes

```code
rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% python3 enrich_menu.py --backend claude --redo --workers 3
34 rows to process with backend claude, 3 at a time
[1/34] CARROLL'S KITCHEN  https://www.carrollskitchen.org/
      -> unknown: No mention of gluten, celiac or GF on the 2 page(s) checked (the menu may be a PDF, an image or another site).
[2/34] THE BIG EASY   https://www.bigeasync.com/
      -> unknown: No mention of gluten, celiac or GF on the 2 page(s) checked (the menu may be a PDF, an image or another site).
[3/34] MECCA RESTAURANT  https://www.mecca-restaurant.com/
      -> unknown: No mention of gluten, celiac or GF on the 3 page(s) checked (the menu may be a PDF, an image or another site).
[4/34] DEATH AND TAXES  https://ac-restaurants.com/death-taxes/
      -> unknown: No mention of gluten, celiac or GF on the 1 page(s) checked (the menu may be a PDF, an image or another site).
[5/34] ORO RESTAURANT   LOUNGE  https://ororaleigh.com/menu/
      -> yes: "Boasting a menu that features a variety of rotating global style tapas and small plates, ORO offers something for everyone with vegetarian, vegan and
[6/34] GRAVY  https://www.gravyraleigh.com/menu/lunch
      -> yes: "Italian Chopped Cucumber, cherry tomatoes, mozzarella, olives, salami, and red onions, balsamic drizzle over romaine heart lettuce with house-made It
[7/34] MANHATTAN CAFE  https://manhattancafe.com/menu/
      -> yes: "Choose 1 Bread White, Hoagie, Wrap/Tortilla, Wheat, Rye, Gluten-free (+$2.00)"
[8/34] RALEIGH TIMES BAR  https://www.morningtimes-raleigh.com/menus
      -> unknown: Model said 'yes' but its quote was not found on the pages, so it was discarded. Read the pages by hand.
[9/34] CENTRO  https://www.centroraleigh.com/
      -> unknown: No mention of gluten, celiac or GF on the 2 page(s) checked (the menu may be a PDF, an image or another site).
[10/34] CAFFE LUNA  https://cafeluna.com/
      -> unknown: No mention of gluten, celiac or GF on the 2 page(s) checked (the menu may be a PDF, an image or another site).
[11/34] 42 LAWRENCE  http://www.42lawrence.com/
      -> unknown: Page has almost no readable text (probably needs JavaScript); read it by hand.
[12/34] SIR WALTER COFFEE  https://raleigh.sirwaltercoffee.com/
      -> unknown: Could not fetch page (curl: (6) Could not resolve host: raleigh.sirwaltercoffee.com).
[13/34] KOKORO RAMEN & IZAKAYA  http://kokororamennc.com
      -> unknown: No mention of gluten, celiac or GF on the 1 page(s) checked (the menu may be a PDF, an image or another site).
[14/34] SONO SUSHI BAR  https://sonoraleigh.com/
      -> yes: "Miso Soup 6 Seafood Mushroom, Wakame, Tofu, Green Onion Gluten Free"
[15/34] MUSTANG HOUSE  http://mustanghouseraleigh.com
      -> unknown: No mention of gluten, celiac or GF on the 1 page(s) checked (the menu may be a PDF, an image or another site).
[16/34] WOODY'S AT CITY MARKET  https://woodyscitymarket.com/
      -> unknown: Page returned HTTP 403.
[17/34] DAIJOBU  https://www.daijobu-raleigh.com/
      -> unknown: No mention of gluten, celiac or GF on the 2 page(s) checked (the menu may be a PDF, an image or another site).
[18/34] BIG ED'S CITY MARKET RESTAURANT  http://www.bigedscitymarket.com/
      -> unknown: No mention of gluten, celiac or GF on the 1 page(s) checked (the menu may be a PDF, an image or another site).
[19/34] WHISKEY KITCHEN  https://www.whiskey.kitchen/
      -> yes: "Boiled Peanuts spice-boiled, pickled peppers $ 7 gluten free, vegan"
[20/34] EL RODEO RESTAURANT  https://elrodeorest.com/
      -> unknown: No mention of gluten, celiac or GF on the 2 page(s) checked (the menu may be a PDF, an image or another site).
[21/34] CREMA CAFE AND EATERY  https://cremaandmore.com/
      -> unknown: Page has almost no readable text (probably needs JavaScript); read it by hand.
[22/34] BREWERY BHAVANA  https://brewerybhavana.com/food-menu/
      -> yes: "BAMBOO SHOOT SALAD $15.80 Delicate tofu skin and crisp bamboo shoots, infused with aromatic herbs and spices, dressed in a house made chili oil (Vega
[23/34] VIC'S ITALIAN CAFE  http://www.vicsitalianrestaurant.com/
      -> unknown: Page returned HTTP 401.
[24/34] POOLE'S DINER  http://ac-restaurants.com/pooles/
      -> unknown: No mention of gluten, celiac or GF on the 1 page(s) checked (the menu may be a PDF, an image or another site).
[25/34] STARBUCKS #75937  https://www.starbucks.com/store-locator/store/7706/marriott-downtown-raleigh-500-fayetteville-st-mall-raleigh-nc-276012118-us
      -> unknown: Page has almost no readable text (probably needs JavaScript); read it by hand.
[26/34] RYE BAR   SOUTHERN KITCHEN  https://www.ryeraleigh.com/
      -> unknown: Page has almost no readable text (probably needs JavaScript); read it by hand.
[27/34] CITY MARKET SUSHI  http://citymarketsushi.com/
      -> unknown: No mention of gluten, celiac or GF on the 2 page(s) checked (the menu may be a PDF, an image or another site).
[28/34] CHIDO TACO #3  https://chidotaco.com/
      -> unknown: Page returned HTTP 403.
[29/34] SIR WALTER COFFEE 555  https://sirwaltercoffee.com/
      -> unknown: No mention of gluten, celiac or GF on the 3 page(s) checked (the menu may be a PDF, an image or another site).
[30/34] LIBATIONS 317  https://www.libations317.com/
      -> unknown: No mention of gluten, celiac or GF on the 1 page(s) checked (the menu may be a PDF, an image or another site).
[31/34] JUBALA COFFEE #4  https://www.jubalacoffee.com/
      -> unknown: No mention of gluten, celiac or GF on the 2 page(s) checked (the menu may be a PDF, an image or another site).
[32/34] ELEMENT GASTROPUB  https://elementgastropub.com/menu
      -> yes: "GF: gluten-free option available."
[33/34] A PLACE AT THE TABLE  https://tableraleigh.org/menu/
      -> limited: "Turkey sausage and gluten-free bread available upon request." Cross-contact note: Our kitchen is NOT gluten-free and cross-contact may occur.
[34/34] THE PIT  https://www.thepit-raleigh.com/menu/lunch
      -> yes: "Southern-Style Deviled Eggs Sweet relish and paprika $ 9.50 gluten-free"

Done: limited=1, unknown=25, yes=8
These are machine suggestions (verified=no). Open each evidence_url, check the quote, then set verified=yes in annotations.csv.
Next: python3 fetch_data.py && python3 gf_map.py

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% vi r

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% mv r rOld

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% vi r

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% ll
total 232
drwxr-xr-x@  3 rippy  staff     96 Oct 19 11:54 __pycache__/
drwxr-xr-x@ 16 rippy  staff    512 Oct 19 12:13 ./
drwxr-xr-x@ 13 rippy  staff    416 Oct 19 12:08 ../
-rw-r--r--@  1 rippy  staff   9443 Oct 19 11:52 annotate.py
-rw-r--r--@  1 rippy  staff  11313 Oct 19 12:12 annotations.csv
drwxr-xr-x@  9 rippy  staff    288 Oct 19 11:21 archive/
drwxr-xr-x@  9 rippy  staff    288 Oct 19 11:55 data/
-rw-r--r--@  1 rippy  staff  22941 Oct 19 12:07 enrich_menu.py
-rw-r--r--@  1 rippy  staff  17572 Oct 19 11:52 fetch_data.py
-rw-r--r--@  1 rippy  staff   1850 Oct 19 11:52 GF_FINDER_README.md
-rwxr-xr-x@  1 rippy  staff    643 Oct 19 11:52 gf_finder.sh*
-rw-r--r--@  1 rippy  staff  22283 Oct 19 11:52 gf_map.py
-rw-r--r--@  1 rippy  staff   1981 Oct 19 11:27 map.html
-rw-r--r--@  1 rippy  staff     65 Oct 19 12:13 r
-rwxr-xr-x@  1 rippy  staff    126 Oct 19 11:54 rOld*
-rwxr-xr-x@  1 rippy  staff     49 Oct 19 11:28 serve*

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% cat ant

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% view annotations.csv

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% ls
__pycache__/         archive/             fetch_data.py        gf_map.py            rOld*
annotate.py          data/                GF_FINDER_README.md  map.html             serve*
annotations.csv      enrich_menu.py       gf_finder.sh*        r

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% chmod +x r

rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% cat r
#!/usr/bin/env bash

python3 fetch_data.py && python3 gf_map.py


rippy@mbp ~/ws/src/github.com/rippy/ato_mlh_2026/scripts
% ./r
Cache was built for [35.7776, -78.6393] / 8000 m; refreshing everything for [35.7776, -78.6393] / 3000 m
OpenStreetMap gluten-free tags (Overpass) ...
  ok: https://overpass-api.de/api/interpreter
OpenStreetMap restaurants with websites (Overpass) ...
  ok: https://overpass-api.de/api/interpreter
Wake County restaurants ...
81 facilities need inspection history
inspections: fetching 81 facilities ...
  layer 1: 81/81 facilities, 1200 rows
violations: fetching 81 facilities ...
  layer 2: 81/81 facilities, 6858 rows

Done. Cached in /Users/rippy/ws/src/github.com/rippy/ato_mlh_2026/scripts/data
Cache for centre [35.7776, -78.6393], radius 3000 m
  osm               1 KB  fetched 2026-10-19T16:14:59+00:00  1 elements
  places           68 KB  fetched 2026-10-19T16:15:43+00:00  150 elements
  wake            178 KB  fetched 2026-10-19T16:15:43+00:00  430 facilities
  inspections     348 KB  fetched 2026-10-19T16:15:46+00:00  1200 rows / 81 facilities
  violations     1575 KB  fetched 2026-10-19T16:15:57+00:00  6858 rows / 81 facilities
  vendor          144 KB  fetched 2026-10-19T16:15:58+00:00  leaflet 1.9.4

Next: python3 gf_map.py
   OSM: 1 places have a gluten-free tag: 1 offer it, 0 say 'no'
   County: 430 permitted food establishments downloaded
   80 annotation rows; 9 gluten-free places within 1200 m (71 other annotated places hidden: no / unknown)
   inspections: 1200 rows, score field=SCORE, date field=DATE_
   violations:  6858 rows, date field=INSPECTDATE
     113 m  yes      unverified ORO RESTAURANT   LOUNGE
     158 m  yes      unverified GRAVY
     173 m  yes      unverified SONO SUSHI BAR
     173 m  yes      unverified MANHATTAN CAFE
     215 m  yes      unverified BREWERY BHAVANA
     275 m  yes      unverified WHISKEY KITCHEN
     354 m  yes      unverified ELEMENT GASTROPUB
     453 m  limited  unverified A PLACE AT THE TABLE
     519 m  yes      unverified THE PIT
   good=0, mixed=9, poor=0, nodata=0  -> map.html
```

Regenerate (see above) then to view the map <http://localhost:8000/map.html>

```shell
cd scripts
python3 -m http.server 8000
```

EOF

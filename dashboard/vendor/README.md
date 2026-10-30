# Vendored libraries

Served from the dashboard itself (no CDN at runtime; the page's CSP allows only its own origin). Pinned versions,
downloaded once from npm via jsDelivr. Update by replacing the file and this table.

| File | Package | License | Used by |
|---|---|---|---|
| d3-array.min.js | d3-array 3.2.4 | ISC | Map tab (dependency of d3-geo) |
| d3-geo.min.js | d3-geo 3.1.1 | ISC | Map tab: orthographic globe projection |
| topojson-client.min.js | topojson-client 3.1.0 | ISC | Map tab: decode country shapes |
| countries-50m.json, countries-110m.json | world-atlas 2.0.2 (Natural Earth 1:50m and 1:110m) | ISC / public domain | Map tab: every country (fine borders when still, coarse while dragging) |
| earth-2k.jpg, earth-4k.jpg | NASA Blue Marble (via three-globe 2.31.0 example images), recompressed | Public domain (NASA Earth Observatory; credit shown on the map) | Map tab: satellite globe |
| pdf.min.mjs, pdf.worker.min.mjs | pdfjs-dist 4.10.38 (legacy build) | Apache-2.0 | Settings: read an uploaded resume PDF in the browser |

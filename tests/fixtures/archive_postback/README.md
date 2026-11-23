Minimal pages for tests/archive/test_postback.py, rewritten by hand from the shapes of real school pages (read only:
research/segment_demo_2026-10-24/raw, and the 10-24 archive's frontier.db and WARC opened read-only; spe's pager from
the 10-24 evidence copy, since the 10-24 archive holds no spe page). Titles and values are made up; no person's name,
no student number. The form state values (__VIEWSTATE …) are short placeholders, one per page, so a test can tell
which page a form was built from.

clv2_files.html and clv2_download_export.html (10-27, design §23 A4): a cLv2 unit site's downloads table as its GET
brings it and as the press of uDataContentPos2$downloadBtn0 answers it (the same page with the iframeExport script), cut
down from the 10-26 pilot's WARC (archive-pilot/, read only: sso.nknu.edu.tw/site/100061, index.aspx?clv2=100061&page=4)
to the form, the language buttons, the type select, two rows and the script. The two document titles are the school's
own (shortened; no person's name, no student number), the form state a placeholder. The pilot holds no answer to the
export GET (the press then stopped at the page): the tests make that answer up.

clv2_timeline_home.html and ajaxanalysis_json.js (10-27, design §23 C): a cLv2 unit site's home as the 10-26 pilot's WARC
holds it (archive-pilot/, read only: sso.nknu.edu.tw/site/100061, index.aspx?clv2=100061), cut down to the template's
inline script (getTimeLine(): json('/Unit/Info/ajaxServices/ajaxTimeline.aspx', {cFunction: "getTimelineJson", pLv2:
cLv2}, …)), the `var cLv2 = '100061';` line, the breadcrumb menu (page titles only), a made-up line of body text and
the timeline box; the form state a placeholder. ajaxanalysis_json.js is json() as /include/js/extend_NKNU/ajaxAnalysis.js
writes it in the same archive (its POST, JSON.stringify body, contentType and dataType). No person's name, no student
number. The pilot holds no answer of the timeline (the crawler never asked it): the tests make its answers up in the
two shapes json() reads.

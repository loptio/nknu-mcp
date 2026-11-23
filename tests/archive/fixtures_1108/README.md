Minimal pages for tests/archive/test_round1_gaps_1108.py (the fill-in of round one's gaps ①–④, 11-08), written by
hand from the shapes of round one's exported pages (read only: SSO WebIdx 书表 pages under the /site/ keys, the
学务处 osa 高教深耕 event pages, the 法规汇编 pages). Titles, file names and values are made up; no person's name, no
student number. The form state values (__VIEWSTATE …) are short placeholders, one per page, so a test can tell which
page a form was built from.

- webidx_items.html: a WebIdx 书表 page (menuid=N) as a /site/ key's seed brings it after its redirect: two rows of
  .items-list-wrapper, each with a ucAllTypeList$rptItems$ctlNN$lnkDownloadFile button, the 全部 and category tabs,
  and an .items-pagination box naming its own page only.
- osa_event.html: a 高教深耕 event page (hesp/eventlist.aspx?event=N): form1 with an encrypted view state and no
  __EVENTVALIDATION, the attachment table #EventDetails1_EventAttachmentList with two EventAttachmentD links, the empty
  hidden frame DownloadEventAtt after it, and the 「關閉」 BackList link whose onclick closes the window.
- regulations.html: a 法规汇编 list page (default.aspx?cPara=…): SSO's master form uForm with the language buttons, the
  search box and button, the unit select, and the table #ctl00_phMain_reggv with one row whose name links a
  previewPdf.aspx address and one whose name has no address and a 「檔案下載」 downloadBtn instead.
- software_list.html: the licensed software list (sso.nknu.edu.tw/software/campussoftware.aspx, for
  test_software_1108.py, gap ⑪): SSO's master form uForm with no __EVENTTARGET field, the language buttons, the
  「清單檢視」 link, and two .act-box icons (軟體甲, 軟體乙) whose icon is an ImageButton (<input type="image">).
- software_page.html: a software's own page (campussoftware.aspx?csoftid=7, 軟體甲): 「返回」, 「格狀檢視」, three
  folder buttons (Windows, Mac, 安裝說明), the Windows folder open with one installer link on the school's host. The
  tests make the 安裝說明 answer and 軟體乙's page from it.

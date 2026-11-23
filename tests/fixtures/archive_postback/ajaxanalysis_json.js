function json(cUrl, oData, SucessCallBack) {
	if (jLoading) jLoading.show();
	$.ajax({
		type: "POST",
		url: cUrl,
		data: typeof oData === "string" ? oData : JSON.stringify(oData),
		contentType: "application/json",
		dataType: 'json',
        error: function (xhr, textStatus, errorThrown) {
			console.log("error", cUrl, oData, xhr, textStatus, errorThrown);
			return;
		},
		success: function (oReturn) {
			console.log("success", oReturn);
			if (jLoading) jLoading.hide();

			let bIsNewType = oReturn.result === undefined;
			let bIsError = bIsNewType ? oReturn.bErrOn : (oReturn.result.bErrOn === "true");
			if (bIsError) {
				let cErrorMessage = bIsNewType ? oReturn.cMessage : oReturn.result.cMessage;
				showAllSiteDialog({ cTitle: "系統訊息", cBody: cErrorMessage, oButtons: [{ cHtml: "確定" }] }); return;
			} else if (typeof SucessCallBack === "function") {
				SucessCallBack(bIsNewType ? oReturn.oData : oReturn.data);
			}
		}
	});
}

define(["loading"], function (loading) {
    "use strict";

    return function (view) {
        var fieldset = view.querySelector("fieldset");
        var input = view.querySelector("#caressMetadataDirectory");
        var destroyed = false;
        var url = ApiClient.getUrl("caress/configuration");

        function showError(response) {
            if (destroyed) return;
            return Emby.importModule("./modules/common/responsehelper.js").then(function (helper) {
                return helper.handleErrorResponse(response);
            });
        }

        view.addEventListener("viewshow", function () {
            fieldset.disabled = true;
            loading.show();
            ApiClient.getJSON(url).then(function (options) {
                if (!destroyed) {
                    input.value = options.MetadataDirectory;
                    fieldset.disabled = false;
                }
            }).catch(showError).finally(function () { loading.hide(); });
        });

        view.addEventListener("viewdestroy", function () { destroyed = true; });

        view.querySelector(".btnDirectoryPicker").addEventListener("click", function () {
            Emby.importModule("./modules/directorybrowser/directorybrowser.js").then(function (DirectoryBrowser) {
                if (destroyed) return;
                var picker = new DirectoryBrowser();
                return picker.show({
                    path: input.value,
                    includeFiles: false,
                    callback: function (path) {
                        input.value = path;
                        picker.close();
                    }
                });
            }).catch(showError);
        });

        view.querySelector("form").addEventListener("submit", function (event) {
            event.preventDefault();
            fieldset.disabled = true;
            loading.show();
            ApiClient.ajax({
                type: "POST",
                url: url,
                contentType: "application/json",
                data: JSON.stringify({ MetadataDirectory: input.value })
            }).then(function () {
                if (!destroyed) return Emby.importModule("./modules/toast/toast.js").then(function (toast) {
                    if (!destroyed) toast("已保存");
                });
            }).catch(showError).finally(function () {
                if (!destroyed) fieldset.disabled = false;
                loading.hide();
            });
        });
    };
});

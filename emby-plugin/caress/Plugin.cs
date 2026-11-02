using System;
using System.Collections.Generic;
using System.IO;
using Emby.Web.GenericEdit;
using MediaBrowser.Common;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Drawing;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Plugins.UI;

namespace caress;

public sealed class PluginOptions : EditableOptionsBase
{
    public override string EditorTitle => "Caress";
    public override string EditorDescription => "";

    public string MetadataDirectory { get; set; } = "/caress-metadata";
}

public sealed class Plugin : BasePluginSimpleUI<PluginOptions>, IHasThumbImage, IHasWebPages, IHasUIPages
{
    public static Plugin? Instance { get; private set; }
    public Plugin(IApplicationHost applicationHost) : base(applicationHost) => Instance = this;
    public override string Name => "Caress";
    public override string Description => "读取本地元数据和图片，文件变更后自动刷新。";
    public override Guid Id => new Guid("d6a2ef14-910b-4aa3-b2a8-ded970e55dc1");
    public PluginOptions Options => GetOptions();

    // Use Emby's JSON options store without registering its tabbed editor.
    IReadOnlyCollection<IPluginUIPageController> IHasUIPages.UIPageControllers => Array.Empty<IPluginUIPageController>();

    public IEnumerable<PluginPageInfo> GetPages()
    {
        yield return new PluginPageInfo { Name = "caress", DisplayName = "Caress", EmbeddedResourcePath = "caress.configuration.html", IsMainConfigPage = true };
        yield return new PluginPageInfo { Name = "caress.js", EmbeddedResourcePath = "caress.configuration.js" };
    }

    public void UpdateOptions(PluginOptions options)
    {
        if (string.IsNullOrWhiteSpace(options.MetadataDirectory) || !Path.IsPathRooted(options.MetadataDirectory) || !Directory.Exists(options.MetadataDirectory))
            throw new ArgumentException("请选择可访问的元数据目录。");
        SaveOptions(options);
    }

    public Stream GetThumbImage() => GetType().Assembly.GetManifestResourceStream("caress.icon.png")!;
    public ImageFormat ThumbImageFormat => ImageFormat.Png;
}

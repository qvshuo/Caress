using System;
using System.ComponentModel;
using System.IO;
using Emby.Web.GenericEdit;
using Emby.Web.GenericEdit.Validation;
using MediaBrowser.Common;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Drawing;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Attributes;

namespace erosia;

public sealed class PluginOptions : EditableOptionsBase
{
    public override string EditorTitle => "";
    public override string EditorDescription => "";

    [DisplayName("元数据目录")]
    [EditFolderPicker]
    public string MetadataDirectory { get; set; } = "/erosia-metadata";

    protected override void Validate(ValidationContext context)
    {
        if (string.IsNullOrWhiteSpace(MetadataDirectory) || !Path.IsPathRooted(MetadataDirectory) || !Directory.Exists(MetadataDirectory))
            context.AddValidationError(nameof(MetadataDirectory), "请选择可访问的元数据目录。");
    }
}

public sealed class Plugin : BasePluginSimpleUI<PluginOptions>, IHasThumbImage
{
    public static Plugin? Instance { get; private set; }
    public Plugin(IApplicationHost applicationHost) : base(applicationHost) => Instance = this;
    public override string Name => "erosia";
    public override string Description => "读取本地元数据和图片，文件变更后自动刷新。";
    public override Guid Id => new Guid("d6a2ef14-910b-4aa3-b2a8-ded970e55dc1");
    public PluginOptions Options => GetOptions();
    public Stream GetThumbImage() => GetType().Assembly.GetManifestResourceStream("erosia.icon.png")!;
    public ImageFormat ThumbImageFormat => ImageFormat.Png;
}

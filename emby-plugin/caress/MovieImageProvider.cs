using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.Configuration;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.IO;
using MediaBrowser.Model.Logging;

namespace caress;

public sealed class MovieImageProvider : ILocalImageFileProvider, IHasOrder
{
    private readonly IFileSystem fileSystem;
    private readonly ILogger logger;
    public MovieImageProvider(IFileSystem fileSystem, ILogManager logManager)
    {
        this.fileSystem = fileSystem;
        logger = logManager.GetLogger("caress");
    }
    public string Name => "Caress";
    public int Order => 0;
    public bool Supports(BaseItem item) => item is Movie;

    public List<LocalImageInfo> GetImages(BaseItem item, LibraryOptions libraryOptions, IDirectoryService directoryService, CancellationToken cancellationToken)
    {
        var images = new List<LocalImageInfo>();
        cancellationToken.ThrowIfCancellationRequested();
        if (!Supports(item)) return images;
        try
        {
            string? folder = !string.IsNullOrWhiteSpace(item.ContainingFolderPath) ? item.ContainingFolderPath : Path.GetDirectoryName(item.Path);
            string? target = MetadataLocator.Locate(Plugin.Instance?.Options.MetadataDirectory ?? "", folder);
            if (target == null) return images;
            void Add(string path, ImageType type)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (MetadataLocator.IsRegularFile(path)) images.Add(new LocalImageInfo { FileInfo = fileSystem.GetFileInfo(path), Type = type });
            }
            foreach (var image in Files(target, item.Path)) Add(image.Path, image.Type);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            logger.Warn("caress images skipped for {0}: {1}", item.Path, exception.GetType().Name);
        }
        return images;
    }

    public static IEnumerable<(string Path, ImageType Type)> Files(string target, string mediaPath)
    {
        string stem = Path.GetFileNameWithoutExtension(mediaPath);
        string nfo = Path.Combine(target, stem + ".nfo");
        if (!MetadataLocator.IsRegularFile(nfo)) return Enumerable.Empty<(string, ImageType)>();
        NfoReader.Read(nfo, Path.GetFileName(target));
        string prefix = stem + "-";
        var allowed = new[] { ".jpg", ".jpeg", ".png", ".gif", ".webp" };
        return Directory.EnumerateFiles(target)
            .Where(path => MetadataLocator.IsRegularFile(path) && allowed.Contains(Path.GetExtension(path).ToLowerInvariant()))
            .Select(path => (Path: path, Stem: Path.GetFileNameWithoutExtension(path)))
            .Where(image => image.Stem.StartsWith(prefix, StringComparison.Ordinal))
            .Select(image => (image.Path, Role: image.Stem.Substring(prefix.Length)))
            .Where(image => image.Role == "poster" || image.Role == "fanart" ||
                (image.Role.StartsWith("fanart", StringComparison.Ordinal) && int.TryParse(image.Role.Substring(6), out int index) && index >= 1 && index <= 100))
            .OrderBy(image => image.Role == "poster" ? -1 : image.Role == "fanart" ? 0 : int.Parse(image.Role.Substring(6)))
            .Select(image => (image.Path, image.Role == "poster" ? ImageType.Primary : ImageType.Backdrop));
    }
}

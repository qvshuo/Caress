using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.Configuration;
using MediaBrowser.Model.IO;
using MediaBrowser.Model.Logging;

namespace caress;

public sealed class MovieProvider : ILocalMetadataProvider<Movie>, IHasOrder
{
    private readonly ILogger logger;
    public MovieProvider(ILogManager logManager)
    {
        logger = logManager.GetLogger("caress");
    }
    public string Name => "Caress";
    public int Order => 0;

    public Task<MetadataResult<Movie>> GetMetadata(ItemInfo info, LibraryOptions libraryOptions, IDirectoryService directoryService, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        try
        {
            string? folder = !string.IsNullOrWhiteSpace(info.ContainingFolderPath) ? info.ContainingFolderPath : Path.GetDirectoryName(info.Path);
            string? target = MetadataLocator.Locate(Plugin.Instance?.Options.MetadataDirectory ?? "", folder);
            string? path = target == null ? null : Path.Combine(target, Path.GetFileNameWithoutExtension(info.Path) + ".nfo");
            if (path != null && MetadataLocator.IsRegularFile(path)) return Task.FromResult(NfoReader.Read(path, Path.GetFileName(target)));
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            logger.Warn("caress NFO skipped for {0}: {1}", info.Path, exception.GetType().Name);
        }
        return Task.FromResult(new MetadataResult<Movie> { HasMetadata = false });
    }
}

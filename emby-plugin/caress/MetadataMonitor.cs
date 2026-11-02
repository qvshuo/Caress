using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.IO;
using MediaBrowser.Model.Logging;

namespace caress;

public sealed class MetadataMonitor : IServerEntryPoint, IDisposable
{
    private readonly ILibraryManager library;
    private readonly IProviderManager providers;
    private readonly IFileSystem fileSystem;
    private readonly ILogger logger;
    private readonly object gate = new object();
    private readonly Dictionary<string, DateTime> pending = new Dictionary<string, DateTime>(StringComparer.Ordinal);
    private FileSystemWatcher? watcher;
    private Timer? timer;
    private string root = "";
    private bool stopped;
    private bool recover;

    public MetadataMonitor(ILibraryManager library, IProviderManager providers, IFileSystem fileSystem, ILogManager logManager)
    {
        this.library = library;
        this.providers = providers;
        this.fileSystem = fileSystem;
        logger = logManager.GetLogger("caress");
    }

    public void Run()
    {
        timer = new Timer(Tick, null, Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
        timer.Change(TimeSpan.Zero, Timeout.InfiniteTimeSpan);
    }

    private void Changed(object sender, FileSystemEventArgs args)
    {
        lock (gate)
        {
            if (stopped || !ReferenceEquals(sender, watcher)) return;
            try
            {
                AddPath(args.FullPath);
                if (args is RenamedEventArgs renamed) AddPath(renamed.OldFullPath);
            }
            catch (Exception exception) when (exception is IOException || exception is UnauthorizedAccessException)
            {
                logger.Warn("caress metadata event unreadable: {0}", exception.GetType().Name);
                recover = true;
            }
        }
    }

    private void AddPath(string path)
    {
        string relative = path.Substring(root.Length).TrimStart(Path.DirectorySeparatorChar);
        string[] parts = relative.Split(Path.DirectorySeparatorChar);
        if (parts.Any(part => part.StartsWith(".", StringComparison.Ordinal))) return;
        if (parts.Length == 1 && Directory.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0)
        {
            // A directory can arrive already populated, before recursive watches are attached.
            foreach (string file in Directory.EnumerateFiles(path)) AddPath(file);
            return;
        }
        if (parts.Length != 2) return;
        string extension = Path.GetExtension(parts[1]).ToLowerInvariant();
        if (extension != ".nfo" && extension != ".jpg" && extension != ".jpeg" && extension != ".png" && extension != ".webp" && extension != ".gif") return;
        string key = parts[0] == "actors" ? "actor:" + Path.GetFileNameWithoutExtension(parts[1]) : "movie:" + parts[0];
        pending[key] = DateTime.UtcNow;
    }

    private void WatcherError(object sender, ErrorEventArgs args)
    {
        lock (gate)
        {
            if (stopped || !ReferenceEquals(sender, watcher)) return;
            logger.Warn("caress metadata watcher interrupted: {0}", args.GetException().GetType().Name);
            recover = true;
        }
    }

    private void Tick(object? state)
    {
        lock (gate)
        {
            if (stopped) return;
            try
            {
                string configured = Plugin.Instance?.Options.MetadataDirectory ?? "";
                configured = Path.IsPathRooted(configured) ? Path.GetFullPath(configured) : "";
                if (configured != root || watcher == null || recover || !Directory.Exists(root))
                {
                    if (watcher != null && configured == root && !Directory.Exists(root)) recover = true;
                    watcher?.Dispose();
                    watcher = null;
                    if (configured != root) pending.Clear();
                    root = configured;
                    if (!Directory.Exists(root)) return;
                    watcher = new FileSystemWatcher(root)
                    {
                        IncludeSubdirectories = true,
                        NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.DirectoryName
                    };
                    watcher.Created += Changed;
                    watcher.Changed += Changed;
                    watcher.Deleted += Changed;
                    watcher.Renamed += Changed;
                    watcher.Error += WatcherError;
                    watcher.EnableRaisingEvents = true;
                    logger.Info("caress watching metadata: {0}", root);
                    if (recover)
                    {
                        // Overflow can lose individual changes; reconcile the local outputs once.
                        foreach (string folder in Directory.EnumerateDirectories(root))
                            if ((File.GetAttributes(folder) & FileAttributes.ReparsePoint) == 0)
                                foreach (string path in Directory.EnumerateFiles(folder)) AddPath(path);
                        recover = false;
                    }
                }
                string[] ready = pending.Where(pair => DateTime.UtcNow - pair.Value >= TimeSpan.FromSeconds(3)).Select(pair => pair.Key).ToArray();
                if (ready.Length == 0) return;
                var movies = ready.Any(key => key.StartsWith("movie:", StringComparison.Ordinal))
                    ? library.GetItemList(new InternalItemsQuery { IncludeItemTypes = new[] { "Movie" }, Recursive = true }) : Array.Empty<BaseItem>();
                var moviesByCode = movies.Where(item => item is Movie && !string.IsNullOrWhiteSpace(item.Path))
                    .ToLookup(item => Path.GetFileName(Path.GetDirectoryName(item.Path)), StringComparer.Ordinal);
                foreach (string key in ready)
                {
                    string name = key.Substring(6);
                    var items = key.StartsWith("actor:", StringComparison.Ordinal)
                        ? library.GetItemList(new InternalItemsQuery { IncludeItemTypes = new[] { "Person" }, Name = name })
                            .Where(item => item is Person && string.Equals(item.Name, name, StringComparison.Ordinal))
                        : moviesByCode[name];
                    foreach (BaseItem item in items)
                    {
                        if (item is Movie)
                        {
                            string? folder = !string.IsNullOrWhiteSpace(item.ContainingFolderPath) ? item.ContainingFolderPath : Path.GetDirectoryName(item.Path);
                            string? target = MetadataLocator.Locate(root, folder);
                            string? nfo = target == null ? null : Path.Combine(target, Path.GetFileNameWithoutExtension(item.Path) + ".nfo");
                            if (nfo == null || !MetadataLocator.IsRegularFile(nfo)) continue;
                            try { NfoReader.Read(nfo, name); }
                            catch (Exception exception)
                            {
                                logger.Warn("caress automatic refresh skipped invalid NFO for {0}: {1}", item.Path, exception.GetType().Name);
                                continue;
                            }
                        }
                        providers.QueueRefresh(item.InternalId, new MetadataRefreshOptions(fileSystem)
                        {
                            MetadataRefreshMode = item is Movie ? MetadataRefreshMode.FullRefresh : MetadataRefreshMode.ValidationOnly,
                            ImageRefreshMode = MetadataRefreshMode.FullRefresh,
                            ReplaceAllMetadata = item is Movie,
                            // Local images are external read-only files. Emby's replacement
                            // operation deletes old files; normal discovery updates their timestamps.
                            ReplaceAllImages = false,
                            EnableRemoteContentProbe = false,
                            EnableSubtitleDownloading = false,
                            EnableThumbnailImageExtraction = false,
                            IsAutomated = true
                        }, RefreshPriority.Normal);
                        logger.Info("caress queued local refresh: {0} ({1})", item.Name, item.InternalId);
                    }
                    pending.Remove(key);
                }
            }
            catch (Exception exception)
            {
                logger.Warn("caress metadata monitor failed: {0}", exception.GetType().Name);
            }
            finally
            {
                // Schedule after completion so slow queries cannot accumulate timer callbacks.
                if (!stopped) timer?.Change(TimeSpan.FromSeconds(1), Timeout.InfiniteTimeSpan);
            }
        }
    }

    public void Dispose()
    {
        lock (gate)
        {
            stopped = true;
            timer?.Dispose();
            watcher?.Dispose();
            pending.Clear();
        }
    }
}

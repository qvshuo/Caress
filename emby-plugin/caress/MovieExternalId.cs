using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.Entities;

namespace caress;

public sealed class MovieExternalId : IExternalId
{
    public string Name => "番号";
    public string Key => "caress";
    public string UrlFormatString => "";
    public bool Supports(IHasProviderIds item) => item is Movie;
}

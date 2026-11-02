using MediaBrowser.Controller.Net;
using MediaBrowser.Model.Services;

namespace caress;

[Route("/caress/configuration", "GET")]
public sealed class GetConfiguration : IReturn<PluginOptions> { }

[Route("/caress/configuration", "POST")]
public sealed class SaveConfiguration : IReturnVoid
{
    public string MetadataDirectory { get; set; } = "";
}

[Authenticated(Roles = "Admin")]
public sealed class ConfigurationService : IService
{
    public object Get(GetConfiguration request) => Plugin.Instance!.Options;

    public void Post(SaveConfiguration request) => Plugin.Instance!.UpdateOptions(
        new PluginOptions { MetadataDirectory = request.MetadataDirectory });
}

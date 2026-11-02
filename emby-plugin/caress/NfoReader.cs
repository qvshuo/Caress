using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Xml;
using System.Xml.Linq;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.Entities;

namespace caress;

public static class NfoReader
{
    public static MetadataResult<Movie> Read(string path, string? expectedCode = null)
    {
        using var stream = File.OpenRead(path);
        using var reader = XmlReader.Create(stream, new XmlReaderSettings
        {
            DtdProcessing = DtdProcessing.Prohibit,
            XmlResolver = null,
            MaxCharactersInDocument = 4 * 1024 * 1024
        });
        XElement root = XDocument.Load(reader).Root ?? throw new XmlException("Empty NFO");
        if (root.Name != "movie") throw new XmlException("Expected movie NFO");
        string? Text(string name) => root.Element(name)?.Value.Trim();
        string title = Text("title") ?? "";
        if (string.IsNullOrWhiteSpace(title)) throw new XmlException("Missing title");
        var movie = new Movie { Name = title, OriginalTitle = Text("originaltitle"), Overview = Text("plot") };
        var result = new MetadataResult<Movie> { Item = movie, HasMetadata = true };
        // Empty NFO people must clear old credits, rather than leave them unspecified.
        result.ResetPeople();
        var identifiers = root.Elements("uniqueid").Where(x => (string?)x.Attribute("type") == "caress").Select(x => x.Value).ToArray();
        string? number = identifiers.FirstOrDefault();
        if (expectedCode != null && (identifiers.Length == 0 || identifiers.Any(value => !string.Equals(value, expectedCode, StringComparison.Ordinal))))
            throw new XmlException("NFO number differs from directory");
        if (!string.IsNullOrWhiteSpace(number)) movie.SetProviderId("caress", number);
        if (int.TryParse(Text("year"), out int year) && year > 0 && year <= 9999) movie.ProductionYear = year;
        if (DateTime.TryParseExact(Text("premiered"), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var date))
        {
            movie.PremiereDate = date;
            movie.ProductionYear = date.Year;
        }
        if (int.TryParse(Text("runtime"), NumberStyles.None, CultureInfo.InvariantCulture, out int minutes) && minutes > 0)
            movie.RunTimeTicks = checked((long)minutes * TimeSpan.TicksPerMinute);
        foreach (var studio in root.Elements("studio").Select(x => x.Value.Trim()).Where(x => x.Length > 0)) movie.AddStudio(studio);
        movie.SetTags(root.Elements("tag").Select(x => x.Value.Trim()).Where(x => x.Length > 0).Distinct());
        foreach (var genre in root.Elements("genre").Select(x => x.Value.Trim()).Where(x => x.Length > 0).Distinct()) movie.AddGenre(genre);
        string? series = root.Element("set")?.Element("name")?.Value.Trim();
        if (!string.IsNullOrWhiteSpace(series)) movie.AddCollection(series);
        foreach (var name in root.Elements("director").Select(x => x.Value.Trim()).Where(x => x.Length > 0).Distinct())
            result.AddPerson(new PersonInfo { Name = name, Type = PersonType.Director });
        foreach (var name in root.Elements("actor").Select(x => x.Element("name")?.Value.Trim()).Where(x => !string.IsNullOrWhiteSpace(x)).Distinct())
            result.AddPerson(new PersonInfo { Name = name, Type = PersonType.Actor });
        return result;
    }
}

FROM mcr.microsoft.com/dotnet/sdk:10.0.402 AS build
WORKDIR /build
COPY emby-plugin/caress ./emby-plugin/caress
RUN dotnet publish emby-plugin/caress/caress.csproj -c Release -o /out
FROM scratch AS artifact
COPY --from=build /out/caress.dll /caress.dll

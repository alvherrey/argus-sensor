# Directorio para bases de datos GeoIP

Este directorio contiene las bases de datos necesarias para el enriquecimiento de flows con información geográfica.

## Archivos requeridos

- `delegated-ipv4-latest` - Base de datos de country codes (RIR)
- `GeoLite2-ASN.mmdb` - Base de datos de ASN de MaxMind

## Instalación

Ejecuta el script de descarga:

```bash
./download-geoip.sh
```

O descarga manualmente:

1. **Country Codes (gratuito)**:
   ```bash
   curl -o ./geoip-data/delegated-ipv4-latest \
     https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest
   ```

2. **GeoLite2 ASN (requiere cuenta gratuita)**:
   - Registrarse en: https://www.maxmind.com/en/geolite2/signup
   - Descargar GeoLite2-ASN.mmdb
   - Colocar en `./geoip-data/GeoLite2-ASN.mmdb`

## Actualización

Las bases de datos deben actualizarse periódicamente:
- Country codes: mensual
- GeoLite2: semanal (automático con cron)

Este directorio se monta en `/usr/share/GeoIP` dentro del contenedor.

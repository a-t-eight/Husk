/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Exercise the private production MAC checker without importing any keys. */
#include "../../src/translation-layer-next/husk-tl-codesign.c"

void tl_log_line(const char *fmt, ...) { (void)fmt; }

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    if (fseek(f, 0, SEEK_END)) return 2;
    long length = ftell(f);
    if (length <= 0 || length > 1024 * 1024 || fseek(f, 0, SEEK_SET)) return 2;
    uint8_t *data = malloc((size_t)length);
    if (!data || fread(data, 1, (size_t)length, f) != (size_t)length) return 2;
    fclose(f);
    int result = pkcs12_mac_check(data, (size_t)length, argv[2]);
    free(data);
    printf("%d\n", result);
    return 0;
}

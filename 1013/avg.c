#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

/*
 * AVG custom instruction reference implementation.
 *
 * Semantics:
 *   rd = floor((rs1 + rs2) / 2)
 *
 * The sum is calculated as int64_t so that adding two int32_t values
 * cannot overflow before the division.
 */
static int32_t avg_floor(int32_t rs1, int32_t rs2)
{
    int64_t sum = (int64_t)rs1 + (int64_t)rs2;
    int64_t result = sum / 2;

    /*
     * C integer division truncates toward zero. Adjust negative odd
     * sums to obtain mathematical floor division.
     */
    if (sum < 0 && (sum % 2) != 0) {
        --result;
    }

    return (int32_t)result;
}

int main(void)
{
    int32_t rs1;
    int32_t rs2;

    printf("AVG: floor((rs1 + rs2) / 2)\n");
    printf("Enter two signed 32-bit integers: ");

    if (scanf("%" SCNd32 " %" SCNd32, &rs1, &rs2) != 2) {
        fprintf(stderr, "Error: expected two signed 32-bit integers.\n");
        return 1;
    }

    printf("rd = %" PRId32 "\n", avg_floor(rs1, rs2));
    return 0;
}

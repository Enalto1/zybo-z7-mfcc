#include "arm_support.h"

int main(void)
{
    if (arm_startup("ARM_HELLO_DDR_V1") == 0) {
        arm_ready_breakpoint();
        arm_complete(ARM_ERROR_NONE);
    }
    arm_result_breakpoint();
    arm_idle();
}

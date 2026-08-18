/* scanf_target.c — stdin 输入验证目标。
 * 从 stdin 读一个字符串到 g_buf，然后停进 after_scanf（断点验证点）。
 * 测试：launch(stdin_data="hello\n") → 断点 after_scanf → 读 g_buf 应含 "hello"。 */
#include <stdio.h>
#define EXPORT __declspec(dllexport)

EXPORT char g_buf[64];

EXPORT void after_scanf(void) { }

int main(void) {
    printf("waiting for input...\n");
    fflush(stdout);
    if (scanf("%63s", g_buf) != 1) {
        printf("scanf failed\n");
        return 1;
    }
    after_scanf();
    printf("got: %s\n", g_buf);
    fflush(stdout);
    return 0;
}

from elfpy.utils.outputs import number_to_string as fmt

precision = 4
print_format = f"8,.{precision}g"
numbers_to_try = [1e18, 1e15, 1e12, 1e9, 1e6, 1e3, 100, 10, 1, 0.1, 0.01]
print(f"{numbers_to_try=}")
numbers_in_reverse = numbers_to_try.copy()
numbers_in_reverse.reverse()
print(f"{numbers_in_reverse=}")
full_list = numbers_to_try + [-x for x in numbers_in_reverse]
print(f"{full_list=}")

for i in full_list:
    print(f"string :{print_format}: {i:{print_format}} --- number_to_string: {fmt(i, precision=precision)}")

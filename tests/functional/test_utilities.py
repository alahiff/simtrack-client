import pytest
import tempfile
import os.path

import simvue.utilities as sv_util

@pytest.mark.utilities
@pytest.mark.parametrize(
    "is_file,hash", [
        (True, "c7be1ed902fb8dd4d48997c6452f5d7e509fbcdbe2808b16bcf4edce4c07d14e"),
        (False, "50ea7be4eeb62777c05b06e21e9e5f6e8ce39442e7efbd17da8d57d5b18f5035")
    ]
)
def test_calculate_hash(is_file: bool, hash: str) -> None:
    if is_file:
        with tempfile.TemporaryDirectory() as tempd:
            with open(out_file := os.path.join(tempd, "temp.txt"), "w") as out_f:
                out_f.write("This is a test")
            assert sv_util.calculate_sha256(filename=out_file, is_file=is_file) == hash
    else:
        assert sv_util.calculate_sha256(filename="temp.txt", is_file=is_file) == hash

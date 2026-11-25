import re

from mlpa.core import config
from mlpa.core.consts.openapi import APP_DESCRIPTION


def test_every_error_code_is_in_the_api_docs_table():
    documented = {int(c) for c in re.findall(r"^\| (\d+) \|", APP_DESCRIPTION, re.M)}
    codes = {v for k, v in vars(config).items() if k.startswith("ERROR_CODE_")}
    # 7 has no constant, it's reserved for Fastly
    assert documented == codes | {7}

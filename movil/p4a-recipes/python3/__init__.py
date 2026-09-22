from pythonforandroid.recipes.python3 import Python3Recipe

class _LocalPython3(Python3Recipe):
    configure_args = Python3Recipe.configure_args + [
        'ac_cv_func_getgrgid=no',
        'ac_cv_func_getgrgid_r=no',
        'ac_cv_func_getgrnam=no',
        'ac_cv_func_getgrnam_r=no',
        'ac_cv_func_getgrent=no',
        'ac_cv_func_setgrent=no',
        'ac_cv_func_endgrent=no',
    ]

recipe = _LocalPython3()
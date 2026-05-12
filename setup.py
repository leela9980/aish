from setuptools import setup


setup(
    name="aish",
    version="0.1.0",
    py_modules=["aish"],
    install_requires=["openai", "pyyaml", "rapidfuzz", "rich"],
    entry_points={
        "console_scripts": [
            "aish=aish:main",
        ]
    },
)

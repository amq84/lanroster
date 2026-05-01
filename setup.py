from setuptools import setup, find_packages

setup(
    name="lanroster",
    version="0.1.0",
    description="Manage and monitor your network device roster",
    author="Abel Mqueralto",
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=[
        "click>=8.0",
        "rich>=13.0",
        "netifaces>=0.11",
    ],
    extras_require={
        "scan": ["scapy>=2.5"],
    },
    entry_points={
        "console_scripts": [
            "lanroster=lanroster.cli:main",
        ],
    },
)

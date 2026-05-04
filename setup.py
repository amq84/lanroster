from setuptools import setup, find_packages

setup(
    name="lanroster",
    version="0.3.0",
    description="Git-backed CLI to manage and monitor your network device roster",
    author="Abel Moreno",
    author_email="abelmqueralto@gmail.com",
    url="https://github.com/amq84/lanroster",
    license="MIT",
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=[
        "click>=8.0",
        "rich>=13.0",
        "netifaces>=0.11",
    ],
    extras_require={
        "vendor": ["mac-vendor-lookup>=0.1.12"],
        "scan": ["scapy>=2.5"],
        "mcp": ["mcp>=1.0"],
        "full": ["mac-vendor-lookup>=0.1.12", "scapy>=2.5", "mcp>=1.0"],
    },
    entry_points={
        "console_scripts": [
            "lanroster=lanroster.cli:main",
            "lanroster-mcp=lanroster.mcp_server:serve",
        ],
    },
    classifiers=[
        "Environment :: Console",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Networking",
        "Topic :: System :: Systems Administration",
    ],
)

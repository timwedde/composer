# composer
`composer` is an application for automatic music generation, building on models provided by the [Magenta](https://github.com/tensorflow/magenta) project.

#### Screenshot
![Application Screenshot](https://github.com/timwedde/composer/blob/master/screenshot.png "Application Screenshot")

## Download
* [Master Version](https://github.com/timwedde/composer/archive/master.zip)
* [Latest Release](https://github.com/timwedde/composer/archive/0.4.0.zip) (0.4.0)

## Installation
composer can be installed by cloning the repository:
```
$ git clone https://github.com/timwedde/composer.git
$ cd composer/
```

Please note that composer requires Python 3 and will not provide backwards compatibility for Python 2.

## Usage
It is recommended to use [virtualenv](https://pypi.org/project/virtualenv/) with this project:
```
$ virtualenv venv -p python3
$ source venv/bin/activate
```
This project supports [direnv](https://direnv.net) to automatically load the virtual environment when entering the project directory.

Once activated, install the required dependencies:
```
$ pip install -r requirements.txt
```

After this, the application can be started:
```
$ python main.py
```

## Contributors

### Contributors on GitHub
* [Contributors](https://github.com/timwedde/composer/graphs/contributors)

### Third party libraries
* [TensorFlow](https://github.com/tensorflow/tensorflow/) Machine Learning Framework
* [Magenta](https://github.com/tensorflow/magenta) Machine Learning Models for Music and Painting
* [Urwid](http://urwid.org) Console user interface library for Python
* [Mingus](https://github.com/bspaans/python-mingus) Music Theory package for Python

## License
* see [LICENSE](https://github.com/timwedde/composer/blob/master/LICENSE) file

## Version
* Version 0.4.0

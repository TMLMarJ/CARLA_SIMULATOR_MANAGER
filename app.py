import multiprocessing

from gui.application import Manager


def main():
    multiprocessing.freeze_support()
    app = Manager()
    app.mainloop()


if __name__ == "__main__":
    main()

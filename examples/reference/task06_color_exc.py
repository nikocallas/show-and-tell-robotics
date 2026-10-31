def run(scene):
    for obj in scene.objects(color="red"):
        pick(obj)
        place("left_bin")
    for obj in scene.objects(color="green"):
        pick(obj)
        place("right_bin")
